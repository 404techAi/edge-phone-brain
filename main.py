import os
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
BOOKING_URL = os.getenv("BOOKING_URL")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

app = FastAPI()


@app.get("/")
async def root():
    return {"status": "ok", "message": "Edge brain running."}


def _get_speech(form: dict) -> str:
    """
    Safely extract what the caller said from Twilio's POST.
    """
    # Twilio usually sends SpeechResult when using <Gather input="speech">
    speech = form.get("SpeechResult") or form.get("speechResult") or ""
    return str(speech).strip()


@app.post("/twilio/voice", response_class=PlainTextResponse)
async def twilio_voice(request: Request):
    """
    First entry point when the call comes in.
    Greets the caller and asks what they need.
    """
    resp = VoiceResponse()

    gather = Gather(
        input="speech",
        action="/twilio/voice/service",
        method="POST",
        timeout=5
    )
    gather.say(
        "Grooming Company, this is Edge, the shop's digital assistant. "
        "What can I help you with today?",
        voice="Polly.Matthew"  # Twilio will fall back if this voice isn't available
    )
    resp.append(gather)

    # If they say nothing, reprompt once and end.
    resp.say(
        "I'm sorry, I didn't catch that. Please call back when you're ready.",
        voice="Polly.Matthew"
    )

    return str(resp)


@app.post("/twilio/voice/service", response_class=PlainTextResponse)
async def twilio_service(request: Request):
    """
    Handles what the caller said they need (haircut, lining, etc.).
    Then asks for their name.
    """
    form = await request.form()
    speech = _get_speech(form)

    resp = VoiceResponse()

    if speech:
        resp.say(
            f"Got it. You said, {speech}.",
            voice="Polly.Matthew"
        )

    gather = Gather(
        input="speech",
        action="/twilio/voice/name",
        method="POST",
        timeout=5
    )
    gather.say(
        "Can I get your first name, please?",
        voice="Polly.Matthew"
    )
    resp.append(gather)

    resp.say(
        "I'm sorry, I didn't catch that. Please call back when you're ready.",
        voice="Polly.Matthew"
    )

    return str(resp)


@app.post("/twilio/voice/name", response_class=PlainTextResponse)
async def twilio_name(request: Request):
    """
    Handles the caller's name and asks for preferred time.
    """
    form = await request.form()
    name = _get_speech(form)

    resp = VoiceResponse()

    if name:
        resp.say(
            f"Thank you, {name}.",
            voice="Polly.Matthew"
        )

    gather = Gather(
        input="speech",
        action="/twilio/voice/time",
        method="POST",
        timeout=5
    )
    gather.say(
        "What day and time works best for your appointment?",
        voice="Polly.Matthew"
    )
    resp.append(gather)

    resp.say(
        "I'm sorry, I didn't catch that. Please call back when you're ready.",
        voice="Polly.Matthew"
    )

    return str(resp)


@app.post("/twilio/voice/time", response_class=PlainTextResponse)
async def twilio_time(request: Request):
    """
    Handles the caller's preferred time and texts them the booking link.
    Then ends the call politely.
    """
    form = await request.form()
    time_pref = _get_speech(form)
    from_number = form.get("From")

    resp = VoiceResponse()

    if time_pref:
        resp.say(
            f"Perfect. You said, {time_pref}.",
            voice="Polly.Matthew"
        )

    # Send SMS with booking link (if we know their number and have a booking URL)
    try:
        if from_number and BOOKING_URL:
            twilio_client.messages.create(
                body=(
                    "Thanks for calling Grooming Company. "
                    f"Use this link to complete your booking: {BOOKING_URL}"
                ),
                from_=TWILIO_NUMBER,
                to=from_number
            )
    except Exception:
        # We don't want SMS failure to crash the call
        pass

    resp.say(
        "I've just sent a booking link to your phone. "
        "Tap the link to pick your exact time and complete payment. "
        "Thank you for calling Grooming Company. Goodbye.",
        voice="Polly.Matthew"
    )
    resp.hangup()

    return str(resp)
