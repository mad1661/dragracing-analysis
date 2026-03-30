const { initializeApp, cert } = require("firebase-admin/app");
const { getStorage } = require("firebase-admin/storage");
const fs = require("fs");
const path = require("path");

const filePath = process.argv[2] || path.join(__dirname, "..", "dist", "TimingTalk-1.0.0-mac-arm64.dmg");
const destName = process.argv[3] || "downloads/TimingTalk-macOS.dmg";

if (!fs.existsSync(filePath)) {
  console.error("File not found:", filePath);
  process.exit(1);
}

initializeApp({
  storageBucket: "nhra-timing-api.firebasestorage.app",
});

async function upload() {
  const bucket = getStorage().bucket();
  console.log(`Uploading ${path.basename(filePath)} (${(fs.statSync(filePath).size / 1024 / 1024).toFixed(1)} MB)...`);

  await bucket.upload(filePath, {
    destination: destName,
    metadata: {
      contentType: "application/x-apple-diskimage",
      metadata: { firebaseStorageDownloadTokens: "public" },
    },
    public: true,
  });

  const file = bucket.file(destName);
  const [metadata] = await file.getMetadata();
  const url = `https://storage.googleapis.com/${bucket.name}/${destName}`;
  console.log("\nUpload complete!");
  console.log("Public URL:", url);
  console.log("\nUpdate download.html with this URL.");
}

upload().catch((err) => {
  console.error("Upload failed:", err.message);
  console.error("\nIf auth fails, you may need to:");
  console.error("1. Go to Firebase Console > Project Settings > Service Accounts");
  console.error("2. Generate a new private key");
  console.error("3. Set GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json");
  console.error("\nOr upload manually via Firebase Console > Storage");
  process.exit(1);
});
