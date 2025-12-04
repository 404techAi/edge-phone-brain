import os
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv

# Load environment variables (local dev; Render uses its own env)
load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
BOOKING_URL = os.getenv("BOOKING_URL")

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

app = FastAPI()

# Simple in-memory state per call
# { CallSid: {"step": int, "service": str, "name": str, "time_pref": str} }
call_state = {}


def get_or_create_state(call_sid: str) -> dict:
    if call_sid not in call_state:
        call_state[call_sid] = {
            "step": 0,
            "service": None,
            "name": None,
            "time_pref": None,
        }
    return call_state[call_sid]


def get_speech(form: dict) -> str:
    speech = form.get("SpeechResult") or form.get("speechResult") or ""
    return str(speech).strip()


@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Edge fast receptionist running."}


@app.post("/twilio/voice", response_class=PlainTextResponse)
async def twilio_voice(request: Request):
    """
    Main Twilio webhook.
    Step 0: Ask what they need.
    Step 1: Ask for name.
    Step 2: Ask for time preference and send SMS.
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From")
    speech = get_speech(form)

    resp = VoiceResponse()

    # If we somehow don't have a CallSid, bail gracefully
    if not call_sid:
        resp.say(
            "Grooming Company. This is Edge, the shop's digital assistant. "
            "Please call back from a valid number.",
            voice="Polly.Matthew",
        )
        resp.hangup()
        return str(resp)

    state = get_or_create_state(call_sid)
    step = state["step"]

    # FIRST TOUCH: no speech yet, step 0 — greet and ask what they need
    if step == 0 and not speech:
        gather = Gather(
            input="speech",
            action="/twilio/voice",
            method="POST",
            timeout=3,
        )
        gather.say(
            "Grooming Company. This is Edge, the shop's digital assistant. "
            "What can I help you with today?",
            voice="Polly.Matthew",
        )
        resp.append(gather)

        resp.say(
            "I'm sorry, I didn't catch that. Please call back when you're ready.",
            voice="Polly.Matthew",
        )
        return str(resp)

    # STEP 0: They just told us what they need (service description)
    if step == 0 and speech:
        state["service"] = speech

        resp.say(
            "Got you, I can help with that. What's your first name?",
            voice="Polly.Matthew",
        )

        # Move to step 1 to collect name
        state["step"] = 1

        gather = Gather(
            input="speech",
            action="/twilio/voice",
            method="POST",
            timeout=3,
        )
        gather.say(
            "Please tell me your first name.",
            voice="Polly.Matthew",
        )
        resp.append(gather)

        resp.say(
            "I'm sorry, I didn't catch that. Please call back when you're ready.",
            voice="Polly.Matthew",
        )
        return str(resp)

    # STEP 1: They just told us their name
    if step == 1 and speech:
        state["name"] = speech

        # Simple first-name extraction (take first word)
        first_name = speech.split()[0] if speech else "there"

        resp.say(
            f"Nice to meet you, {first_name}. What day and time works best for your appointment?",
            voice="Polly.Matthew",
        )

        # Move to step 2 to collect time preference
        state["step"] = 2

        gather = Gather(
            input="speech",
            action="/twilio/voice",
            method="POST",
            timeout=4,
        )
        gather.say(
            "You can say something like, tomorrow at 3 p.m., or Saturday morning.",
            voice="Polly.Matthew",
        )
        resp.append(gather)

        resp.say(
            "I'm sorry, I didn't catch that. Please call back when you're ready.",
            voice="Polly.Matthew",
        )
        return str(resp)

    # STEP 2: They just told us their time preference
    if step == 2 and speech:
        state["time_pref"] = speech

        # Try to send an SMS with booking link
        if from_number and TWILIO_NUMBER and BOOKING_URL:
            try:
                body = (
                    "Thanks for calling Grooming Company. "
                    f"We noted: '{state['service']}' for '{state['time_pref']}'. "
                    f"Use this link to complete your booking: {BOOKING_URL}"
                )
                twilio_client.messages.create(
                    body=body,
                    from_=TWILIO_NUMBER,
                    to=from_number,
                )
            except Exception as e:
                print("SMS send error:", e)

        resp.say(
            "Perfect. I’ve got that noted. "
            "I just sent a booking link to your phone so you can pick your exact time and complete payment. "
            "Thank you for calling Grooming Company. Goodbye.",
            voice="Polly.Matthew",
        )
        resp.hangup()

        # Clean up this call's state
        call_state.pop(call_sid, None)
        return str(resp)

    # If we reach here, something's off or they were silent at a later step
    resp.say(
        "I'm sorry, something went wrong. Please call back and we’ll get you taken care of.",
        voice="Polly.Matthew",
    )
    resp.hangup()
    call_state.pop(call_sid, None)
    return str(resp)



