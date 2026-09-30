import json
import os
import time

import streamlit as st
from google import genai
from google.genai import types
from twilio.rest import Client as Twilio_Client

from prompts import SYSTEM_PROMPT, WELCOME_MESSAGE, SUMMARY_REQUEST_PROMPT


# ============================================================
# CREDENTIALS
# ============================================================
# Recommended:
# Create a .env file or set these as environment variables.
#
# GEMINI_API_KEY=your_gemini_api_key
# TWILIO_ACCOUNT_SID=your_twilio_account_sid
# TWILIO_AUTH_TOKEN=your_twilio_auth_token
# TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
# TWILIO_CONTENT_SID=your_twilio_content_sid
#
# If you already have these values hardcoded in your local file,
# you can keep them there locally. Do NOT upload them to GitHub.

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.getenv(
    "TWILIO_WHATSAPP_FROM",
    "whatsapp:+14155238886"
)
TWILIO_CONTENT_SID = os.getenv("TWILIO_CONTENT_SID", "")


# ============================================================
# GEMINI MODEL FALLBACK LIST
# ============================================================
# gemini-1.5-flash is no longer suitable for your current setup.
#
# We try the models in order.
# If one model is unavailable, the next model is attempted.

MODEL_NAMES = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash",
]


# ============================================================
# GEMINI CLIENT
# ============================================================
@st.cache_resource
def get_genai_client():
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY is missing. "
            "Please set your Gemini API key."
        )

    return genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# TWILIO CLIENT
# ============================================================
@st.cache_resource
def get_twilio_client():

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        raise ValueError(
            "Twilio credentials are missing. "
            "Please check TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN."
        )

    return Twilio_Client(
        TWILIO_ACCOUNT_SID,
        TWILIO_AUTH_TOKEN
    )


# ============================================================
# INITIALIZE CLIENTS SAFELY
# ============================================================
try:
    gemini_client = get_genai_client()
except Exception as error:
    gemini_client = None
    GEMINI_CLIENT_ERROR = str(error)


try:
    twilio_custom_client = get_twilio_client()
except Exception as error:
    twilio_custom_client = None
    TWILIO_CLIENT_ERROR = str(error)


# ============================================================
# SESSION STATE
# ============================================================
if "messages" not in st.session_state:
    st.session_state.messages = []


if "onboarder" not in st.session_state:
    st.session_state.onboarder = False


if "chat" not in st.session_state:
    st.session_state.chat = None


if "model_name" not in st.session_state:
    st.session_state.model_name = None


# ============================================================
# RENDER MESSAGE
# ============================================================
def render_messages(message):

    with st.chat_message(message["role"]):

        if message["kind"] == "text":
            st.write(message["content"])

        elif message["kind"] == "image":
            st.image(message["content"])


# ============================================================
# ADD MESSAGE
# ============================================================
def add_message(role, kind, content):

    st.session_state.messages.append(
        {
            "role": role,
            "kind": kind,
            "content": content
        }
    )

    render_messages(
        st.session_state.messages[-1]
    )


# ============================================================
# CREATE GEMINI CHAT
# ============================================================
def create_gemini_chat():

    if gemini_client is None:
        raise Exception(
            "Gemini client could not be initialized. "
            "Please check your GEMINI_API_KEY."
        )

    last_error = None

    # Try each model one by one
    for model_name in MODEL_NAMES:

        try:

            chat = gemini_client.chats.create(
                model=model_name,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT
                )
            )

            return chat, model_name

        except Exception as error:

            last_error = error

            # Try the next model
            continue

    raise Exception(
        "None of the available Gemini models could be initialized.\n\n"
        f"Last error:\n{last_error}"
    )


# ============================================================
# ASK GEMINI
# ============================================================
def ask_gemini(parts):

    if not parts:
        return "Please enter a question or upload a meal image."

    if st.session_state.chat is None:

        try:

            (
                st.session_state.chat,
                st.session_state.model_name
            ) = create_gemini_chat()

        except Exception as error:

            return (
                "Gemini could not be initialized.\n\n"
                f"Error: {error}"
            )

    # First attempt
    try:

        response = st.session_state.chat.send_message(
            message=parts
        )

        if response is None:
            return "Gemini returned an empty response."

        if not getattr(response, "text", None):
            return "Gemini returned an empty response."

        return response.text

    except Exception as first_error:

        # ====================================================
        # AUTOMATIC RECOVERY
        # ====================================================
        # Sometimes a model/chat session can fail after it has
        # already been created. We create a fresh chat and retry.

        try:

            (
                new_chat,
                new_model
            ) = create_gemini_chat()

            st.session_state.chat = new_chat
            st.session_state.model_name = new_model

            time.sleep(1)

            response = st.session_state.chat.send_message(
                message=parts
            )

            if response is None:
                return "Gemini returned an empty response."

            if not getattr(response, "text", None):
                return "Gemini returned an empty response."

            return response.text

        except Exception as second_error:

            return (
                "Sorry, something went wrong while contacting Gemini.\n\n"
                f"First attempt: {first_error}\n\n"
                f"Retry attempt: {second_error}"
            )


# ============================================================
# CLEAN WHATSAPP TEXT
# ============================================================
def clean_whatsapp_text(text):

    if not text:
        return "No nutrition summary available."

    text = " ".join(
        str(text).split()
    )

    if len(text) > 1500:
        return text[:1500] + "..."

    return text


# ============================================================
# SEND WHATSAPP
# ============================================================
def send_whatsapp(to_number, user_name, summary):

    if twilio_custom_client is None:

        return (
            False,
            "Twilio client is not configured correctly."
        )

    try:

        # Remove whatsapp: if user accidentally enters it
        cleaned_number = str(to_number).strip()

        if cleaned_number.startswith("whatsapp:"):
            cleaned_number = cleaned_number.replace(
                "whatsapp:",
                "",
                1
            )

        # Make sure it begins with +
        if not cleaned_number.startswith("+"):
            return (
                False,
                "Please enter the WhatsApp number with country code, "
                "for example +91XXXXXXXXXX."
            )

        content_variables = json.dumps(
            {
                "1": user_name,
                "2": clean_whatsapp_text(summary)
            },
            ensure_ascii=False
        )

        message = twilio_custom_client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=f"whatsapp:{cleaned_number}",
            content_sid=TWILIO_CONTENT_SID,
            content_variables=content_variables,
        )

        return True, message.sid

    except Exception as error:

        return False, str(error)


# ============================================================
# STEP 1: ONBOARDING
# ============================================================
if not st.session_state.onboarder:

    st.title("Welcome to macroSnap! 🍕")

    st.caption(
        "Please log in to continue."
    )

    # Show Gemini configuration error before login
    if gemini_client is None:

        st.warning(
            "Gemini is not configured yet. "
            "Please check your GEMINI_API_KEY."
        )

    with st.form("login_form"):

        name = st.text_input(
            "Your name"
        )

        whatsapp_number = st.text_input(
            "WhatsApp Number (with country code)",
            placeholder="+91XXXXXXXXXX",
            help=(
                "Please enter your WhatsApp number "
                "with the country code."
            )
        )

        submit_button = st.form_submit_button(
            "Log In"
        )

        if submit_button:

            if not name.strip():

                st.error(
                    "Please enter your name."
                )

            elif not whatsapp_number.strip():

                st.error(
                    "Please enter your WhatsApp number."
                )

            else:

                # --------------------------------------------
                # Try Gemini before completing login
                # --------------------------------------------
                try:

                    (
                        chat,
                        model_name
                    ) = create_gemini_chat()

                    st.session_state.chat = chat
                    st.session_state.model_name = model_name

                    st.session_state.onboarder = True

                    st.session_state.name = name.strip()

                    st.session_state.whatsapp_number = (
                        whatsapp_number.strip()
                    )

                    st.session_state.messages = []

                    st.rerun()

                except Exception as error:

                    st.error(
                        "Gemini could not be connected."
                    )

                    st.code(
                        str(error)
                    )

    st.stop()


# ============================================================
# STEP 2: MAIN INTERFACE
# ============================================================
header_col, button_col = st.columns(
    [5, 2],
    vertical_alignment="center"
)


# ============================================================
# HEADER
# ============================================================
with header_col:

    st.title(
        "Welcome to macroSnap! 🍕"
    )


# ============================================================
# WHATSAPP BUTTON
# ============================================================
with button_col:

    send_disabled = (
        len(st.session_state.messages) <= 1
    )

    if st.button(
        "Send to WhatsApp",
        disabled=send_disabled,
        use_container_width=True
    ):

        with st.spinner(
            "Creating nutrition summary..."
        ):

            summary = ask_gemini(
                [
                    types.Part.from_text(
                        text=SUMMARY_REQUEST_PROMPT
                    )
                ]
            )

        # Don't send an API error to WhatsApp
        if (
            not summary
            or summary.startswith(
                "Sorry, something went wrong"
            )
            or summary.startswith(
                "Gemini could not"
            )
        ):

            st.error(
                "Could not create the nutrition summary."
            )

        else:

            with st.spinner(
                "Sending to WhatsApp..."
            ):

                success, info = send_whatsapp(
                    st.session_state.whatsapp_number,
                    st.session_state.name,
                    summary
                )

            if success:

                st.success(
                    "Message sent successfully!"
                )

            else:

                st.error(
                    f"Failed to send message: {info}"
                )


# ============================================================
# LOGIN INFORMATION
# ============================================================
st.caption(
    f"Logged in as {st.session_state.name} "
    f"- updates go to "
    f"{st.session_state.whatsapp_number}"
)


# ============================================================
# SHOW CURRENT MODEL
# ============================================================
if st.session_state.model_name:

    st.caption(
        f"Gemini model: {st.session_state.model_name}"
    )


# ============================================================
# WELCOME MESSAGE
# ============================================================
if not st.session_state.messages:

    add_message(
        "assistant",
        "text",
        WELCOME_MESSAGE.format(
            name=st.session_state.name
        )
    )

else:

    for message in st.session_state.messages:

        render_messages(message)


# ============================================================
# CHAT INPUT
# ============================================================
user_input = st.chat_input(
    "Ask a Question or Attach a photo of your meal",
    accept_file=True,
    file_type=[
        "image/png",
        "image/jpeg",
        "image/jpg"
    ]
)


# ============================================================
# USER MESSAGE
# ============================================================
if user_input:

    photo = (
        user_input.files[0]
        if user_input.files
        else None
    )

    text = user_input.text

    parts = []


    # ========================================================
    # IMAGE
    # ========================================================
    if photo is not None:

        try:

            photo_bytes = photo.getvalue()

            add_message(
                "user",
                "image",
                photo_bytes
            )

            mime_type = photo.type

            if not mime_type:
                mime_type = "image/jpeg"

            parts.append(
                types.Part.from_bytes(
                    data=photo_bytes,
                    mime_type=mime_type
                )
            )

        except Exception as error:

            st.error(
                f"Could not process the image: {error}"
            )

            st.stop()


    # ========================================================
    # TEXT
    # ========================================================
    if text:

        add_message(
            "user",
            "text",
            text
        )

        parts.append(
            types.Part.from_text(
                text=text
            )
        )


    # ========================================================
    # IMAGE WITHOUT TEXT
    # ========================================================
    elif photo is not None:

        parts.append(
            types.Part.from_text(
                text=(
                    "What is the meal? "
                    "Identify the food in the image and "
                    "give me the calories, nutrition "
                    "information, protein, carbohydrates, "
                    "fat, and other relevant macros."
                )
            )
        )


    # ========================================================
    # GEMINI ANALYSIS
    # ========================================================
    if parts:

        with st.spinner(
            "Analyzing..."
        ):

            answer = ask_gemini(
                parts
            )

        add_message(
            "assistant",
            "text",
            answer
        )