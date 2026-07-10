import streamlit as st

st.set_page_config(page_title="GenA", layout="wide")

home_page   = st.Page("views/home.py", title="Home", icon=":material/home:", default=True)
bot_page    = st.Page("views/bot.py", title="Data Preprocessing", icon=":material/quiz:", url_path="data_preprocessing")
editor_page = st.Page("views/dataset_editor.py", title="Results & Editor", icon=":material/table_chart:")
queue_page  = st.Page("views/queue_manager.py",  title="Queue Manager", icon=":material/queue:")
stats_page  = st.Page("views/statistics.py",     title="Statistics", icon=":material/bar_chart:")
dynamic_page = st.Page("views/dynamic_implementation.py", title="Dynamic Implementation", icon=":material/shuffle:")
docs_page   = st.Page("views/docs.py", title="Documentation", icon=":material/menu_book:")

nav = {
    "GenA Framework": [home_page, bot_page, editor_page, queue_page, stats_page, dynamic_page, docs_page]
}

pg = st.navigation(nav)
pg.run()
