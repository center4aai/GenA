import streamlit as st

st.set_page_config(page_title="GenA", layout="wide")

bot_page    = st.Page("views/bot.py", title="Question Generator")
editor_page = st.Page("views/dataset_editor.py", title="Dataset Editor")
queue_page  = st.Page("views/queue_manager.py",  title="Queue Manager")
stats_page  = st.Page("views/statistics.py",     title="Statistics")
dynamic_implementation = st.Page("views/dynamic_implementation.py", title="Dynamic Implementation")

nav = {
    "GenA Framework": [bot_page, editor_page, queue_page, stats_page, dynamic_implementation]
}

pg = st.navigation(nav)
pg.run()