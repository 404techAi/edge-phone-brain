import os
from fastapi import FastAPI, Form
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
from openai import OpenAI
from dotenv import load_dotenv

# Load .env variables
load_dotenv()

# --- Setup clients ---
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
twilio_client = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))

TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
BOOKING_URL = os.getenv("BOOKING_URL", "https://example.com/book")

app = FastAPI()

# Simple in-memory call state (Day 1 only; resets when app restarts)
call_state = {}

EDGE_SYSTEM_PROMPT = """
You are EDGE, the digital assistant for Grooming Co., a barbershop in Maywood, IL.

Rules:
- You never say you are an AI. You are "the shop's digital assistant."
- Speak like a real person, calm and confident.
- Keep answers short: 1–2 sentences max.
- Always acknowledge what the caller said in your own words.

Goal of this version:
1) Understand what service they want.
2) Get their first name.
3) Get their preferred day/time window.
4) Tell them you'll text them a booking link to finish.

You do NOT quote exact times or prices. Instead say:
"I’ll text you a link so you can finish booking."

Stay friendly, efficient, and natural.
"""

def get_step_state(call_sid: str):
    if call_sid not in call_state:
        call_state[call_sid] = {
            "step": 0,
            "service": None,
            "name": None,
            "time_pref": None
        }
    return call_state[call_sid]

def generate_edge_reply(state, caller_text: str) -> str:
    step = state["step"]

    summary = f"Known: service={state['service']}, name={state['name']}, time_pref={state['time_pref']}."

    if step == 0:
        instruction = (
            "Acknowledge their service in your own words. Then ask 'What's your first name?'"
        )
    elif step == 1:
        instruction = (
            "Acknowledge their name. Then ask: 'What day and roughly what time works best for you?'"
        )
    elif step == 2:
        instruction = (
            "Acknowledge their time preference. Then say you'll text them the booking link and ask if they need anything else."
        )
    else:
        instruction = "Wrap up politely."

    user_content = f"""
Caller said: "{caller_text}"

{summary}

Current step: {step}
Instruction: {instruction}
"""

    response = openai_client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {"role": "system", "content": EDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ]
    )

    edge_text = response.output[0].content[0].text
    return edge_text.strip()

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Edge brain running."}

@app.post("/twilio/voice", response_class=PlainTextResponse)
async def twilio_voice(
    From: str = Form(None),
    CallSid: str = Form(None),
    SpeechResult: str = Form(None)
):
    vr = VoiceResponse()

    # First hit: no speech yet
    if SpeechResult is None:
        gather = Gather(input="speech", action="/twilio/voice", method="POST")
        gather.say("Grooming Company. This is Edge, the shop's digital assistant. What can I help you with today?")
        vr.append(gather)
        vr.redirect("/twilio/voice")
        return str(vr)

    state = get_step_state(CallSid)
    caller_text = SpeechResult.strip()

    # Update state
    if state["step"] == 0:
        state["service"] = caller_text
        state["step"] = 1
    elif state["step"] == 1:
        state["name"] = caller_text
        state["step"] = 2
    elif state["step"] == 2:
        state["time_pref"] = caller_text
        state["step"] = 3

    edge_reply = generate_edge_reply(state, caller_text)

    # Completed steps
    if state["step"] == 3:
        try:
            twilio_client.messages.create(
                to=From,
                from_=TWILIO_NUMBER,
                body=f"Hey {state['name']}, this is Edge from Grooming Co. "
                     f"Tap this link to finish booking your {state['service']}: {BOOKING_URL}"
            )
        except Exception as e:
            print("SMS error:", e)

        vr.say(edge_reply)
        vr.pause(length=1)
        vr.say("Thanks for calling Grooming Company. Talk to you soon.")
        vr.hangup()

        call_state.pop(CallSid, None)
        return str(vr)

    # Keep conversation going
    gather = Gather(input="speech", action="/twilio/voice", method="POST")
    gather.say(edge_reply)
    vr.append(gather)
    vr.redirect("/twilio/voice")

    return str(vr)

