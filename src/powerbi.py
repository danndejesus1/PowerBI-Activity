

import streamlit as st
import os
from dotenv import load_dotenv
from openai import AzureOpenAI

# Load environment variables
load_dotenv()

# Azure OpenAI settings
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)
deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT")

st.set_page_config(layout="wide")
left, right = st.columns([3, 1])

with left:
	st.markdown("### Power BI Report")
	powerbi_url = "https://app.powerbi.com/reportEmbed?reportId=c8ac3fd7-2da8-4a1f-ad4b-dbd548b17e8c&autoAuth=true&ctid=eb25818e-5bd5-49bf-99de-53e3e7b42630"
	st.components.v1.iframe(powerbi_url, width=800, height=600)

with right:
	st.header("Prompt:")
	prompt = st.text_area("Enter your prompt here", height=100, label_visibility="collapsed", key="prompt_box")
	
	generate_btn = st.button("Generate Insights")
	
	st.markdown("### Summary / Insights:")

	if generate_btn and prompt:
		with st.spinner("Generating insights..."):
			try:
				response = client.chat.completions.create(
					model=deployment_name,
					messages=[
						{"role": "system", "content": "You are a helpful assistant that generates insights for Power BI dashboards."},
						{"role": "user", "content": prompt}
					],
					max_tokens=150
				)
				summary = response.choices[0].message.content.strip()
				st.success("Generated successfully!")
				st.write(summary)
			except Exception as e:
				st.error(f"Error: {e}")
