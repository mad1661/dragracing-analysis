const fs = require("fs");
const path = require("path");

const distDir = path.join(__dirname, "..", "dist");
const hostingDir = path.join(__dirname, "..", "..", "cloud", "public", "downloads");

if (!fs.existsSync(hostingDir)) {
  fs.mkdirSync(hostingDir, { recursive: true });
}

const extensions = [".exe", ".dmg", ".AppImage", ".deb", ".zip"];

function copyBuilds() {
  if (!fs.existsSync(distDir)) {
    console.error("No dist/ directory found. Run `npm run build` first.");
    process.exit(1);
  }

  const files = fs.readdirSync(distDir);
  let copied = 0;

  for (const file of files) {
    if (extensions.some(ext => file.endsWith(ext))) {
      const src = path.join(distDir, file);
      const destName = file
        .replace(/TimingTalk-\d+\.\d+\.\d+-/, "TimingTalk-")
        .replace(/-x64/, "");

      let finalName = destName;
      if (destName.endsWith(".exe") && !destName.includes("Setup")) {
        finalName = "TimingTalk-Portable.exe";
      } else if (destName.endsWith(".exe")) {
        finalName = "TimingTalk-Setup.exe";
      } else if (destName.endsWith(".dmg")) {
        finalName = "TimingTalk.dmg";
      } else if (destName.endsWith(".AppImage")) {
        finalName = "TimingTalk.AppImage";
      }

      fs.copyFileSync(src, path.join(hostingDir, finalName));
      console.log(`Copied: ${file} -> downloads/${finalName}`);
      copied++;
    }
  }

  if (copied === 0) {
    console.log("No distributable files found in dist/");
  } else {
    console.log(`\n${copied} file(s) copied to cloud/public/downloads/`);
    console.log("Run 'firebase deploy --only hosting' to publish.");
  }
}

copyBuilds();
