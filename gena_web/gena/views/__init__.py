"""Reusable view helpers for Streamlit pages."""

import streamlit as st


def page_subtitle(text: str) -> None:
    """Unstyled main-page description: same look as the GenA 2.0 tagline on Home."""
    st.markdown(
        f"<p style='font-size:1.15em; color:#555; margin-bottom:1.5em;'>{text}</p>",
        unsafe_allow_html=True,
    )
