const os = require("os");
const path = require("path");
const net = require("net");
const { execSync, spawn } = require("child_process");
const fs = require("fs");

const TCP_PORT = 4001;

function findSocat() {
  const candidates = [
    "/opt/homebrew/bin/socat",
    "/usr/local/bin/socat",
    "/usr/bin/socat",
    "/bin/socat",
  ];
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  try {
    return execSync("which socat", { encoding: "utf-8" }).trim();
  } catch {}
  return "socat";
}

class SerialBridge {
  constructor(portName = "") {
    this._platform = os.platform();
    this._portName = portName;
    this._isRunning = false;
    this._writeStream = null;
    this._socatProcess = null;
    this._tcpServer = null;
    this._tcpClients = [];
    this._tcpPort = TCP_PORT;
    this._devicePath = "";
    this._slavePath = "";
  }

  async start() {
    if (this._isRunning) return;

    switch (this._platform) {
      case "darwin":
        await this._startMac();
        break;
      case "linux":
        await this._startLinux();
        break;
      case "win32":
        await this._startWindows();
        break;
      default:
        throw new Error(`Unsupported platform: ${this._platform}`);
    }

    await this._startTcpServer();
    this._isRunning = true;
  }

  stop() {
    this._isRunning = false;

    if (this._socatProcess) {
      this._socatProcess.kill("SIGTERM");
      this._socatProcess = null;
    }

    if (this._writeStream) {
      try { this._writeStream.end(); } catch {}
      this._writeStream = null;
    }

    if (this._tcpServer) {
      this._tcpClients.forEach(c => { try { c.destroy(); } catch {} });
      this._tcpClients = [];
      try { this._tcpServer.close(); } catch {}
      this._tcpServer = null;
    }

    if (this._platform !== "win32" && this._portName) {
      try {
        if (fs.lstatSync(this._portName).isSymbolicLink()) {
          fs.unlinkSync(this._portName);
        }
      } catch {}
    }
  }

  write(data) {
    if (!this._isRunning) return;

    const buf = typeof data === "string" ? Buffer.from(data + "\r\n", "ascii") : data;

    if (this._writeStream) {
      try { this._writeStream.write(buf); } catch {}
    }

    for (const client of this._tcpClients) {
      try { client.write(buf); } catch {}
    }
  }

  get portName() {
    return this._portName || this._slavePath || "None";
  }

  get devicePath() {
    return this._devicePath;
  }

  get tcpPort() {
    return this._tcpPort;
  }

  get isRunning() {
    return this._isRunning;
  }

  _startTcpServer() {
    return new Promise((resolve) => {
      this._tcpServer = net.createServer((socket) => {
        this._tcpClients.push(socket);
        socket.on("close", () => {
          this._tcpClients = this._tcpClients.filter(c => c !== socket);
        });
        socket.on("error", () => {
          this._tcpClients = this._tcpClients.filter(c => c !== socket);
        });
      });

      this._tcpServer.on("error", (err) => {
        if (err.code === "EADDRINUSE") {
          this._tcpPort++;
          this._tcpServer.listen(this._tcpPort, "127.0.0.1");
        }
      });

      this._tcpServer.listen(this._tcpPort, "127.0.0.1", () => {
        this._tcpPort = this._tcpServer.address().port;
        resolve();
      });
    });
  }

  _resolveTargetPath() {
    const raw = (this._portName || "").trim();
    if (!raw) return "/tmp/ttyTimingTalk";
    if (raw.match(/^COM\d+$/i)) return "/tmp/ttyTimingTalk";
    if (!path.isAbsolute(raw)) return path.join("/tmp", raw);
    return raw;
  }

  async _startMac() {
    await this._startSocat(this._resolveTargetPath());
  }

  async _startLinux() {
    await this._startSocat(this._resolveTargetPath());
  }

  _startSocat(targetPath) {
    return new Promise((resolve, reject) => {
      try { fs.unlinkSync(targetPath); } catch {}

      const targetDir = path.dirname(targetPath);
      if (!fs.existsSync(targetDir)) {
        reject(new Error(`Directory ${targetDir} does not exist. Use a path like /tmp/ttyTimingTalk`));
        return;
      }

      const socatBin = findSocat();
      this._socatProcess = spawn(socatBin, [
        "-d", "-d",
        `PTY,link=${targetPath},raw,echo=0`,
        "PTY,raw,echo=0",
      ], { stdio: ["pipe", "pipe", "pipe"] });

      let ptyCount = 0;
      let resolved = false;
      let stderrLog = "";

      this._socatProcess.stderr.on("data", (chunk) => {
        const text = chunk.toString();
        stderrLog += text;

        const matches = text.matchAll(/N PTY is (.+)/g);
        for (const match of matches) {
          ptyCount++;
          const ptyPath = match[1].trim();

          if (ptyCount === 1) {
            this._devicePath = ptyPath;
          }

          if (ptyCount === 2 && !resolved) {
            this._slavePath = targetPath;
            resolved = true;
            setTimeout(() => {
              try {
                this._writeStream = fs.createWriteStream(ptyPath, { flags: "w" });
                this._portName = targetPath;
                resolve();
              } catch (err) {
                reject(new Error(`Failed to open PTY ${ptyPath}: ${err.message}`));
              }
            }, 500);
          }
        }
      });

      this._socatProcess.on("error", (err) => {
        if (!resolved) {
          reject(new Error(`socat not found. Install it: brew install socat (Mac) or apt install socat (Linux). Error: ${err.message}`));
        }
      });

      this._socatProcess.on("exit", (code) => {
        if (!resolved) {
          const hint = stderrLog.includes("Permission denied") ? " (permission denied)" :
                       stderrLog.includes("No such file") ? " (directory does not exist)" : "";
          reject(new Error(`socat exited with code ${code}${hint}. Path: ${targetPath}`));
        }
        this._isRunning = false;
      });

      setTimeout(() => {
        if (!resolved) {
          reject(new Error("socat startup timed out. stderr: " + stderrLog.substring(0, 200)));
        }
      }, 5000);
    });
  }

  /**
   * Windows: Use com0com virtual null-modem driver.
   * Expects com0com to be installed with a port pair configured.
   * The app writes to one port (e.g. COM10), and user software reads from
   * the paired port (e.g. COM11).
   *
   * If com0com is not installed, falls back to a named pipe approach.
   */
  async _startWindows() {
    const portName = this._portName || "COM10";
    this._portName = portName;

    // Check if com0com port exists
    if (this._hasCom0comPort(portName)) {
      await this._openWindowsPort(portName);
      return;
    }

    // Fallback: create a named pipe that some serial programs can connect to
    const pipePath = `\\\\.\\pipe\\TimingTalk`;
    const net = require("net");
    const server = net.createServer((socket) => {
      // Client connected to pipe, send data to them
      this._pipeClients = this._pipeClients || [];
      this._pipeClients.push(socket);
      socket.on("close", () => {
        this._pipeClients = this._pipeClients.filter((s) => s !== socket);
      });
    });

    server.listen(pipePath);
    this._pipeServer = server;
    this._portName = pipePath;

    this._writeStream = {
      write: (data) => {
        if (this._pipeClients) {
          this._pipeClients.forEach((client) => {
            try { client.write(data); } catch {}
          });
        }
      },
      end: () => {
        if (this._pipeServer) this._pipeServer.close();
      },
    };
  }

  _hasCom0comPort(portName) {
    try {
      const output = execSync("mode", { encoding: "utf-8" });
      return output.includes(portName);
    } catch {
      return false;
    }
  }

  async _openWindowsPort(portName) {
    try {
      const { SerialPort } = require("serialport");
      const port = new SerialPort({ path: portName, baudRate: 9600 });
      this._writeStream = port;
      return new Promise((resolve, reject) => {
        port.on("open", resolve);
        port.on("error", reject);
      });
    } catch {
      throw new Error("serialport module not available. Install com0com and serialport, or use the named pipe fallback.");
    }
  }
}

module.exports = { SerialBridge };
