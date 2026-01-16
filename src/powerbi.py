import streamlit as st

# Set page config for a wide layout
st.set_page_config(layout="wide")

# Create two columns: left (empty, for PowerBI embed) and right (Streamlit UI)
left, right = st.columns([3, 1])

with left:
    st.markdown("### Power BI Report")
    powerbi_url = "https://app.powerbi.com/reportEmbed?reportId=c8ac3fd7-2da8-4a1f-ad4b-dbd548b17e8c&autoAuth=true&ctid=eb25818e-5bd5-49bf-99de-53e3e7b42630"
    st.components.v1.iframe(powerbi_url, width=800, height=600)

with right:
	st.header("Prompt:")
	prompt = st.text_area("Enter your prompt here", height=100, label_visibility="collapsed")
	st.markdown("### Summary / Insights:")
	summary = "Lorem ipsum dolor etc."
	st.text_area("Summary / Insights", summary, height=200, label_visibility="collapsed", disabled=True)
