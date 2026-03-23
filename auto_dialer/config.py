# ============================================================
# Auto-Dialer Configuration
# ============================================================
# Fill in your VoIP/SIP provider credentials below.
# Recommended: VoIP.ms (https://voip.ms) — no monthly fee, ~$0.01/min
#
# Setup steps for VoIP.ms:
#   1. Create account at voip.ms
#   2. Add $5 credit (Settings → Client Area → Finances)
#   3. Create a sub-account (Main Menu → Sub Accounts → Create Sub Account)
#   4. Fill in the values below from your sub-account details
# ============================================================

# SIP Provider Settings
SIP_SERVER = "chicago1.voip.ms"       # Your VoIP.ms server (or your provider's SIP server)
SIP_USERNAME = "YOUR_SUBACCOUNT"      # e.g., "100000_myphone"
SIP_PASSWORD = "YOUR_PASSWORD"        # Your sub-account password
SIP_PORT = 5060                       # Usually 5060

# Target Call Settings
TARGET_NUMBER = "17172951851"         # Number to call (no dashes, with country code)
DTMF_SEQUENCE = "***6"               # Keys to press after greeting starts
SAY_NO_TO_SURVEY = True              # Say "No" when asked about survey

# Timing (seconds) — these get auto-tuned in training mode
WAIT_FOR_GREETING = 5                # Wait this long after call connects before sending DTMF
DTMF_DELAY = 0.5                     # Delay between each DTMF tone
WAIT_FOR_SURVEY = 5                  # Wait this long after DTMF before saying "No"
HUMAN_DETECT_TIMEOUT = 15            # If call stays alive this long after "No", it's a human
RETRY_DELAY = 15                     # Seconds to wait between retry attempts
MAX_RETRIES = 50                     # Maximum number of retry attempts (0 = unlimited)

# Audio
NO_AUDIO_FILE = "audio/no.wav"       # Path to "No" audio file

# Training / Learning
ENABLE_TRAINING = True               # Log call data for auto-tuning
CALL_LOG_FILE = "call_log.json"      # Where to store call attempt data
