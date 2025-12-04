import os
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables (for local dev; Render uses env vars directly)
load_dotenv()

# Environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER")
BOOKING_URL = os.getenv("BOOKING_URL")

# Clients
openai_client = OpenAI(api_key=OPENAI_API_KEY)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

app = FastAPI()

# In-memory call state (per CallSid)
# DO NOT use this for long-term storage – this is just for live calls.
call_state = {}

EDGE_SYSTEM_PROMPT = """
You are EDGE, the digital assistant for Grooming Co., a barbershop in Maywood, IL.

Your job:
- Help callers book haircuts and grooming services.
- Keep the convo smooth, like a real front-desk person at the shop.

Personality rules:
- Never say you are an AI. You are "the shop's digital assistant."
- Sound calm, confident, and human. No stiff or robotic phrases.
- Keep it short: 1–2 sentences max.
- Always show that you understood what they said by paraphrasing, not just repeating word for word.
- Stay focused on booking: service, name, and when they want to come in.

Examples of your style:
- Caller: "Yeah I just need a taper and line this afternoon."
  You: "Got you, a taper and line up this afternoon. Let me grab your first name real quick."

- Caller: "I wanna bring my son in for a cut after school."
  You: "Perfect, a cut for your son after school. What’s your first name so I can start the booking?"

- Caller: "I just moved out here and need somebody to clean me up."
  You: "Welcome to the area, we got you. Sounds like a clean-up cut — what’s your first name so I can get you in the system?"
"""


def get_or_create_call_state(call_sid: str) -> dict:
    """
    Get or initialize the state for a given call.
    State keys: step, service, name, time_pref
    """
    if call_sid not in call_state:
        call_state[call_sid] = {
            "step": 0,
            "service": None,
            "name": None,
            "time_pref": None,
        }
    return call_state[call_sid]


def get_speech(form: dict) -> str:
    """
    Safely extract what the caller said from Twilio's POST.
    """
    speech = form.get("SpeechResult") or form.get("speechResult") or ""
    return str(speech).strip()


def ai_reply(user_instruction: str) -> str:
    """
    Ask OpenAI to generate a short, natural Edge reply.
    """
    try:
        response = openai_client.responses.create(
            model="gpt-5.1-mini",
            input=[
                {"role": "system", "content": EDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_instruction},
            ],
        )
        text = response.output[0].content[0].text.strip()
        return text
    except Exception as e:
        # If anything goes wrong with OpenAI, fall back to a safe generic line.
        print("OpenAI error:", e)
        return "Got you. I can help with that."


@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Edge brain running."}


@app.post("/twilio/voice", response_class=PlainTextResponse)
async def twilio_voice(request: Request):
    """
    Main Twilio webhook for voice.
    Handles all steps based on call state:
    - First hit: greet + ask what they need
    - Step 0: caller said what they need -> AI paraphrase + ask name
    - Step 1: caller said name -> AI acknowledge + ask time
    - Step 2: caller said time -> confirm + SMS booking link + hang up
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From")

    resp = VoiceResponse()

    # If we don't have a CallSid, we can't track state; just do a simple message.
    if not call_sid:
        resp.say(
            "Grooming Company. This is Edge, the shop's digital assistant. Please call back from a valid number.",
            voice="Polly.Matthew",
        )
        resp.hangup()
        return str(resp)

    state = get_or_create_call_state(call_sid)
    step = state["step"]
    speech = get_speech(form)

    # First time this endpoint is hit (no speech yet): greet + ask what they need
    if not speech and step == 0 and "SpeechResult" not in form:
        gather = Gather(
            input="speech",
            action="/twilio/voice",
            method="POST",
            timeout=5,
        )
        gather.say(
            "Grooming Company. This is Edge, the shop's digital assistant. What can I help you with today?",
            voice="Polly.Matthew",
        )
        resp.append(gather)

        resp.say(
            "I'm sorry, I didn't catch that. Please call back when you're ready.",
            voice="Polly.Matthew",
        )
        return str(resp)

    # STEP 0: They just told us what they need (service description)
    if step == 0:
        state["service"] = speech or state["service"]

        instruction = (
            f'The caller just said: "{speech}". '
            "Acknowledge what they want in a natural way in 1–2 sentences, "
            "then smoothly ask for their first name."
        )
        edge_text = ai_reply(instruction)

        resp.say(edge_text, voice="Polly.Matthew")

        # Move to step 1: collect name
        state["step"] = 1

        gather = Gather(
            input="speech",
            action="/twilio/voice",
            method="POST",
            timeout=5,
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
    if step == 1:
        state["name"] = speech or state["name"]

        instruction = (
            f'The caller\'s name is "{state["name"]}". '
            "Acknowledge them by name in a natural way, then ask what day and time works best "
            "for their appointment. Keep it to 1–2 sentences."
        )
        edge_text = ai_reply(instruction)

        resp.say(edge_text, voice="Polly.Matthew")

        # Move to step 2: collect time preference
        state["step"] = 2

        gather = Gather(
            input="speech",
            action="/twilio/voice",
            method="POST",
            timeout=5,
        )
        gather.say(
            "What day and time works best for your appointment?",
            voice="Polly.Matthew",
        )
        resp.append(gather)

        resp.say(
            "I'm sorry, I didn't catch that. Please call back when you're ready.",
            voice="Polly.Matthew",
        )
        return str(resp)

    # STEP 2: They just told us their time preference
    if step == 2:
        state["time_pref"] = speech or state["time_pref"]

        # Try to send SMS with booking link
        try:
            if from_number and TWILIO_NUMBER and BOOKING_URL:
                body = (
                    "Thanks for calling Grooming Company. "
                    f"We noted: {state['service']} for {state['time_pref']}. "
                    f"Use this link to complete your booking: {BOOKING_URL}"
                )
                twilio_client.messages.create(
                    body=body,
                    from_=TWILIO_NUMBER,
                    to=from_number,
                )
        except Exception as e:
            print("SMS send error:", e)

        # Confirm and hang up
        confirm_line = (
            f"Perfect. I’ve noted that for {state['time_pref']}. "
            "I just sent a booking link to your phone so you can pick your exact time and complete payment. "
            "Thank you for calling Grooming Company. Goodbye."
        )
        resp.say(confirm_line, voice="Polly.Matthew")
        resp.hangup()

        # Clean up state
        call_state.pop(call_sid, None)
        return str(resp)

    # Fallback: if something is off, reset the flow politely
    resp.say(
        "I'm sorry, something went wrong. Please call back and we’ll get you taken care of.",
        voice="Polly.Matthew",
    )
    resp.hangup()
    call_state.pop(call_sid, None)
    return str(resp)


