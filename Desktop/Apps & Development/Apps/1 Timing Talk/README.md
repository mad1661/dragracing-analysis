# Timing Talk

Live drag racing timing data from 150+ tracks, streamed to any app, website, or serial software.

**Portal:** [nhra-timing-api.web.app](https://nhra-timing-api.web.app)

## Web Portal

The portal at `nhra-timing-api.web.app` includes:

| Page | URL | Description |
|------|-----|-------------|
| Home | `/` | Overview, quick start, how it works |
| Apps | `/apps.html` | Online tools: live track directory, scoreboards, analytics (coming soon) |
| API Docs | `/docs.html` | Full API reference, SDK docs, code examples |
| Dashboard | `/dashboard.html` | Sign up, manage API keys, view devices |
| Download | `/download.html` | Desktop app for legacy serial software |

## For Developers: Use the API

```html
<script src="https://nhra-timing-api.web.app/timing-talk.js"></script>
<script>
  const tt = new TimingTalk("tt_your_api_key");
  tt.stream("DEVICE_ID", (data) => {
    console.log(data.raw);
  });
</script>
```

Get an API key at [nhra-timing-api.web.app/dashboard.html](https://nhra-timing-api.web.app/dashboard.html).

## API Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /api/tracks` | Public | Browse all tracks |
| `GET /api/tracks/live` | Public | Online tracks only |
| `GET /api/stream/:deviceId` | API Key | SSE live stream |
| `GET /api/latest/:deviceId` | API Key | Latest data snapshot |
| `GET /api/history/:deviceId` | API Key | Historical readings |
| `POST /api/keys` | Firebase Auth | Generate API key |
| `GET /api/keys` | Firebase Auth | List your API keys |
| `DELETE /api/keys/:keyId` | Firebase Auth | Delete API key |

## For Track Operators: Pi Setup

1. Flash Raspberry Pi OS Lite onto a Pi 5
2. Connect the timing serial adapter via USB
3. Run: `curl -sSL https://raw.githubusercontent.com/.../setup.sh | sudo bash`
4. Name your track on the OLED setup or web portal at `http://[pi-ip]:8080`
5. Connect to WiFi (the Pi remembers all networks)
6. Your track appears in the live directory

### Remote Support With Cursor

The recommended setup is `Tailscale + MagicDNS + normal SSH`.

- No public port forwarding is required.
- The Pi can move between different WiFis and still keep the same private SSH name.
- Raspberry Connect can remain enabled as a backup for screen sharing.

#### One-Time Pi Enrollment

If you already have a Tailscale auth key, run the Pi installer like this:

```bash
curl -sSL https://raw.githubusercontent.com/.../setup.sh | sudo TAILSCALE_AUTH_KEY="tskey-..." bash
```

If you do not pass `TAILSCALE_AUTH_KEY`, the installer still installs Tailscale and prints the one-time command to finish login manually:

```bash
sudo tailscale up --accept-dns=true --hostname timingtalk-DEVICEID
```

Each Pi uses a stable hostname based on its Timing Talk `device_id`, like `timingtalk-abcdef12`.

#### Cursor SSH Config

Add an SSH host entry on your laptop:

```sshconfig
Host timingtalk-*
  User pi
  ServerAliveInterval 30
  ServerAliveCountMax 6
```

Then connect from Cursor using the Pi's MagicDNS host, for example:

```bash
ssh pi@timingtalk-abcdef12
```

The Pi setup page and the admin console both show the current Tailscale DNS name and Tailscale IP once connected.

## Desktop App (Legacy)

For software that requires a physical COM/serial port, download the desktop app from [nhra-timing-api.web.app/download.html](https://nhra-timing-api.web.app/download.html).

It creates a virtual serial port and bridges cloud data to it -- your existing software works unchanged.

### Building the Desktop App

```bash
cd desktop
npm install
npm run build          # all platforms
npm run build:win      # Windows .exe
npm run build:mac      # macOS .dmg
npm run build:linux    # Linux .AppImage
npm run dist           # build + copy to cloud/public/downloads
```

## Deploying

```bash
cd cloud
npm install --prefix functions
firebase deploy
```

## Project Structure

```
pi/              Raspberry Pi software (Python)
cloud/           Firebase: functions, hosting, rules
  public/        Web portal (HTML/CSS/JS)
  functions/     Cloud Functions (Node.js API)
desktop/         Electron desktop app
sdk/             JavaScript SDK
```
