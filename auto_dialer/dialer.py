#!/usr/bin/env python3
"""
Auto-Dialer: Navigates IVR phone menus until a human answers.

Calls the target number, sends DTMF tones to navigate menus,
says "No" to survey, and retries until a real person picks up.

Usage:
    python dialer.py                    # Normal mode
    python dialer.py --mark-last human  # Mark last call as human-answered
    python dialer.py --mark-last hangup # Mark last call as automation-hangup
    python dialer.py --show-log         # Show call history
    python dialer.py --tune             # Auto-tune timings from call log

Requires:
    - pjsua CLI tool (brew install pjsip)
    - VoIP/SIP provider credentials in config.py
    - audio/no.wav (run generate_audio.py first)
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

import pexpect

import config


class CallLogger:
    """Logs call attempts and supports auto-tuning of timings."""

    def __init__(self, log_file):
        self.log_file = log_file
        self.log = self._load()

    def _load(self):
        if os.path.exists(self.log_file):
            with open(self.log_file) as f:
                return json.load(f)
        return {"attempts": [], "tuned_timings": {}}

    def _save(self):
        with open(self.log_file, "w") as f:
            json.dump(self.log, f, indent=2)

    def log_attempt(self, attempt_data):
        self.log["attempts"].append(attempt_data)
        self._save()

    def mark_last(self, outcome):
        if self.log["attempts"]:
            self.log["attempts"][-1]["outcome"] = outcome
            self._save()
            print(f"Marked last attempt as: {outcome}")
        else:
            print("No attempts to mark.")

    def show_log(self):
        if not self.log["attempts"]:
            print("No call attempts logged yet.")
            return
        print(f"\n{'#':<4} {'Time':<20} {'Duration':<10} {'Outcome':<12}")
        print("-" * 50)
        for i, a in enumerate(self.log["attempts"], 1):
            print(
                f"{i:<4} {a.get('start_time', 'N/A'):<20} "
                f"{a.get('duration', 'N/A'):<10} "
                f"{a.get('outcome', 'unknown'):<12}"
            )
        print(f"\nTotal attempts: {len(self.log['attempts'])}")

    def get_tuned_timings(self):
        """Analyze call log to suggest better timings."""
        attempts = self.log["attempts"]
        if len(attempts) < 3:
            return None

        hangups = [a for a in attempts if a.get("outcome") == "hangup"]
        humans = [a for a in attempts if a.get("outcome") == "human"]

        timings = {}
        if hangups:
            avg_duration = sum(a.get("duration", 0) for a in hangups) / len(hangups)
            # If automation hangups are fast, we can detect humans sooner
            timings["human_detect_timeout"] = max(avg_duration * 1.5, 8)

        if humans:
            avg_human_duration = sum(a.get("duration", 0) for a in humans) / len(
                humans
            )
            timings["avg_human_call_duration"] = avg_human_duration

        return timings


class AutoDialer:
    """Controls pjsua to make calls and navigate IVR menus."""

    def __init__(self):
        self.process = None
        self.logger = CallLogger(config.CALL_LOG_FILE) if config.ENABLE_TRAINING else None
        self.running = True
        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, signum, frame):
        print("\n\nInterrupted by user. Cleaning up...")
        self.running = False
        self._cleanup()
        sys.exit(0)

    def _cleanup(self):
        if self.process and self.process.isalive():
            try:
                self.process.sendline("qa")
                self.process.expect(pexpect.EOF, timeout=5)
            except Exception:
                self.process.terminate(force=True)

    def _find_pjsua(self):
        """Find the pjsua binary."""
        # Check common locations
        for path in [
            "pjsua",
            "/opt/homebrew/bin/pjsua",
            "/usr/local/bin/pjsua",
            os.path.expanduser("~/pjsua"),
        ]:
            try:
                result = subprocess.run(
                    ["which", path], capture_output=True, text=True
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except FileNotFoundError:
                continue

        # Try just running it
        try:
            subprocess.run(
                ["pjsua", "--help"],
                capture_output=True,
                timeout=5,
            )
            return "pjsua"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return None

    def start_pjsua(self):
        """Start pjsua and register with SIP provider."""
        pjsua_path = self._find_pjsua()
        if not pjsua_path:
            print("ERROR: pjsua not found!")
            print("Install it with: brew install pjsip")
            print("  or download from: https://www.pjsip.org/download.htm")
            sys.exit(1)

        sip_url = f"sip:{config.SIP_USERNAME}@{config.SIP_SERVER}"

        cmd = (
            f"{pjsua_path} "
            f"--id {sip_url} "
            f"--registrar sip:{config.SIP_SERVER}:{config.SIP_PORT} "
            f"--realm * "
            f"--username {config.SIP_USERNAME} "
            f"--password {config.SIP_PASSWORD} "
            f"--null-audio "  # Don't use local audio device initially
            f"--auto-play "
            f"--play-file {os.path.abspath(config.NO_AUDIO_FILE)} "
            f"--duration 0 "
            f"--no-color"
        )

        print(f"Starting pjsua and registering with {config.SIP_SERVER}...")
        self.process = pexpect.spawn(cmd, timeout=30, encoding="utf-8")
        self.process.logfile_read = sys.stdout

        # Wait for registration
        try:
            index = self.process.expect(
                ["registration success", "registration failed", pexpect.TIMEOUT],
                timeout=15,
            )
            if index == 0:
                print("\nRegistered with SIP provider!")
                return True
            elif index == 1:
                print("\nERROR: SIP registration failed. Check your credentials in config.py")
                return False
            else:
                print("\nERROR: SIP registration timed out.")
                return False
        except pexpect.EOF:
            print("\nERROR: pjsua exited unexpectedly.")
            return False

    def make_call(self):
        """Initiate a call to the target number."""
        sip_uri = f"sip:{config.TARGET_NUMBER}@{config.SIP_SERVER}"
        print(f"\nCalling {config.TARGET_NUMBER}...")
        self.process.sendline(f"m {sip_uri}")

        # Wait for call to connect
        try:
            index = self.process.expect(
                [
                    "CONFIRMED",      # Call connected
                    "DISCONNECTED",   # Call failed/rejected
                    "busy",           # Busy signal
                    pexpect.TIMEOUT,
                ],
                timeout=30,
            )
            if index == 0:
                print("Call connected!")
                return True
            elif index == 1:
                print("Call disconnected/rejected.")
                return False
            elif index == 2:
                print("Busy signal.")
                return False
            else:
                print("Call timed out.")
                return False
        except pexpect.EOF:
            print("pjsua exited during call.")
            return False

    def send_dtmf(self, digits):
        """Send DTMF tones one at a time."""
        print(f"Sending DTMF: {digits}")
        for digit in digits:
            self.process.sendline(f"# {digit}")
            time.sleep(config.DTMF_DELAY)
            print(f"  Sent: {digit}")

    def play_no(self):
        """Play the 'No' audio file into the call."""
        print("Saying 'No'...")
        # In pjsua, we can use the auto-play feature or adjust_audio
        # The --play-file flag already loaded no.wav, we just need to connect it
        self.process.sendline("cl")  # conference list
        time.sleep(0.5)
        # Connect the WAV player to the call
        self.process.sendline("cc 0 1")  # connect slot 0 (wav) to slot 1 (call)
        time.sleep(2)
        # Disconnect after playing
        self.process.sendline("cd 0 1")

    def wait_and_detect(self, timeout):
        """Wait and detect if the call is still alive (human) or dropped (automation)."""
        print(f"Monitoring call for {timeout}s...")
        start = time.time()

        while time.time() - start < timeout:
            if not self.running:
                return "interrupted"

            try:
                index = self.process.expect(
                    [
                        "DISCONNECTED",
                        "call has been disconnected",
                        pexpect.TIMEOUT,
                    ],
                    timeout=2,
                )
                if index in (0, 1):
                    elapsed = time.time() - start
                    print(f"Call dropped after {elapsed:.1f}s — automation hung up.")
                    return "hangup"
            except pexpect.EOF:
                return "hangup"

        print(f"Call still alive after {timeout}s — HUMAN DETECTED!")
        return "human"

    def alert_human_detected(self):
        """Alert the user that a human answered."""
        print("\n" + "=" * 60)
        print("  HUMAN ANSWERED! PICK UP YOUR PHONE!")
        print("=" * 60 + "\n")

        # Play system alert sound on macOS
        if sys.platform == "darwin":
            for _ in range(5):
                os.system('afplay /System/Library/Sounds/Glass.aiff &')
                time.sleep(0.5)

        # Keep the script alive so the call stays connected
        print("Press Ctrl+C to end the call and exit.")
        while self.running:
            try:
                index = self.process.expect(
                    ["DISCONNECTED", pexpect.TIMEOUT],
                    timeout=5,
                )
                if index == 0:
                    print("Call ended by remote party.")
                    break
            except pexpect.EOF:
                break

    def run_call_flow(self):
        """Execute the full call flow once. Returns outcome."""
        start_time = datetime.now()

        # Step 1: Make the call
        if not self.make_call():
            return {
                "start_time": start_time.isoformat(),
                "duration": 0,
                "outcome": "failed",
            }

        # Step 2: Wait for greeting
        print(f"Waiting {config.WAIT_FOR_GREETING}s for greeting...")
        time.sleep(config.WAIT_FOR_GREETING)

        # Step 3: Send DTMF sequence (***6)
        self.send_dtmf(config.DTMF_SEQUENCE)

        # Step 4: Wait for survey question
        print(f"Waiting {config.WAIT_FOR_SURVEY}s for survey question...")
        time.sleep(config.WAIT_FOR_SURVEY)

        # Step 5: Say "No" to survey
        if config.SAY_NO_TO_SURVEY:
            self.play_no()

        # Step 6: Monitor for human vs automation
        # Use tuned timings if available
        timeout = config.HUMAN_DETECT_TIMEOUT
        if self.logger:
            tuned = self.logger.get_tuned_timings()
            if tuned and "human_detect_timeout" in tuned:
                timeout = tuned["human_detect_timeout"]
                print(f"  (Using tuned timeout: {timeout:.1f}s)")

        outcome = self.wait_and_detect(timeout)
        duration = (datetime.now() - start_time).total_seconds()

        attempt = {
            "start_time": start_time.isoformat(),
            "duration": round(duration, 1),
            "outcome": outcome,
        }

        if self.logger:
            self.logger.log_attempt(attempt)

        return attempt

    def run(self):
        """Main retry loop."""
        print("=" * 60)
        print("  Auto-Dialer — IVR Navigation Bot")
        print(f"  Target: {config.TARGET_NUMBER}")
        print(f"  DTMF Sequence: {config.DTMF_SEQUENCE}")
        print(f"  Max Retries: {config.MAX_RETRIES or 'unlimited'}")
        print("=" * 60)

        # Verify audio file exists
        if not os.path.exists(config.NO_AUDIO_FILE):
            print(f"\nERROR: {config.NO_AUDIO_FILE} not found!")
            print("Run 'python generate_audio.py' first to create it.")
            sys.exit(1)

        # Start pjsua
        if not self.start_pjsua():
            sys.exit(1)

        attempt_num = 0
        while self.running:
            attempt_num += 1
            max_str = f"/{config.MAX_RETRIES}" if config.MAX_RETRIES else ""
            print(f"\n--- Attempt {attempt_num}{max_str} ---")

            result = self.run_call_flow()

            if result["outcome"] == "human":
                self.alert_human_detected()
                break
            elif result["outcome"] == "interrupted":
                break
            else:
                if config.MAX_RETRIES and attempt_num >= config.MAX_RETRIES:
                    print(f"\nReached max retries ({config.MAX_RETRIES}). Giving up.")
                    break

                print(f"Waiting {config.RETRY_DELAY}s before retrying...")
                time.sleep(config.RETRY_DELAY)

        self._cleanup()
        print("\nDone.")


def main():
    # Handle CLI arguments
    if "--show-log" in sys.argv:
        logger = CallLogger(config.CALL_LOG_FILE)
        logger.show_log()
        return

    if "--mark-last" in sys.argv:
        idx = sys.argv.index("--mark-last")
        if idx + 1 < len(sys.argv):
            logger = CallLogger(config.CALL_LOG_FILE)
            logger.mark_last(sys.argv[idx + 1])
        else:
            print("Usage: python dialer.py --mark-last [human|hangup]")
        return

    if "--tune" in sys.argv:
        logger = CallLogger(config.CALL_LOG_FILE)
        tuned = logger.get_tuned_timings()
        if tuned:
            print("Suggested timings based on call history:")
            for k, v in tuned.items():
                print(f"  {k}: {v:.1f}s")
        else:
            print("Not enough data yet. Need at least 3 logged attempts.")
        return

    # Run the dialer
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    dialer = AutoDialer()
    dialer.run()


if __name__ == "__main__":
    main()
