import streamlit as st
from gena.http import post, cm_set_auth, cm_del_auth

st.title("Sign in")

username = st.text_input("Username")
password = st.text_input("Password", type="password")

c1, c2 = st.columns([1,1])

with c1:
    if st.button("Login", type="primary"):
        try:
            resp = post("/auth/login", json={"username": username, "password": password})
        except Exception as e:
            st.error(f"Auth error: {e}")
        else:
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("access_token") or data.get("token")
                role = data.get("role", "user")
                st.session_state["token"] = token
                st.session_state["role"] = role
                st.session_state["username"] = username
                cm_set_auth(token, role, username, days=30)
                st.success("Успешный вход!")
                st.stop()

            else:
                st.error(f"Login failed: {resp.status_code} — {resp.text}")

with c2:
    if st.button("Logout"):
        st.session_state.clear()
        cm_del_auth()
        st.switch_page("login.py")