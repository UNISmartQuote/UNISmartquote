import os
from collections.abc import Mapping

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError


def _section(name):
    try:
        value = st.secrets[name]
    except (KeyError, StreamlitSecretNotFoundError):
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"Streamlit secret section [{name}] must be a table.")
    return value


def database_url():
    configured = os.environ.get("UI_QUOTE_DATABASE_URL")
    if configured:
        return configured.strip()
    value = _section("database").get("url")
    return str(value).strip() if value else None


def ai_settings():
    values = _section("ai")
    return {
        "provider": str(values.get("provider", "ollama")).strip().lower(),
        "base_url": str(values.get("base_url", "")).strip(),
        "model": str(values.get("model", "")).strip(),
        "api_key": str(values.get("api_key", "")),
    }


def bootstrap_admin():
    values = _section("bootstrap_admin")
    username = str(values.get("username", "")).strip()
    display_name = str(values.get("display_name", "")).strip()
    password = str(values.get("password", ""))
    if not any((username, display_name, password)):
        return None

    missing = []
    if not username:
        missing.append("username")
    if not display_name:
        missing.append("display_name")
    if not password.strip():
        missing.append("password")
    if missing:
        required = ", ".join(f"bootstrap_admin.{name}" for name in missing)
        raise ValueError(f"Missing Streamlit secrets: {required}.")
    return {
        "username": username,
        "display_name": display_name,
        "password": password,
    }
