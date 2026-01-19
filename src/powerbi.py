import streamlit as st
import os
import requests
from dotenv import load_dotenv
from openai import AzureOpenAI
import msal
import easyocr
from PIL import Image
import io
import pyautogui
import numpy as np
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)
deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

AAD_TENANT_ID = os.getenv("AAD_TENANT_ID")
AAD_CLIENT_ID = os.getenv("AAD_CLIENT_ID")
AAD_CLIENT_SECRET = os.getenv("AAD_CLIENT_SECRET")

POWERBI_WORKSPACE_ID = os.getenv("POWERBI_WORKSPACE_ID")
POWERBI_DATASET_ID = os.getenv("POWERBI_DATASET_ID")
POWERBI_REPORT_ID = os.getenv("POWERBI_REPORT_ID")

DEFAULT_TIMEOUT = 30
session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"]
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

def get_powerbi_access_token():
    if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]):
        return None, "Missing Azure AD credentials"
    
    try:
        app = msal.ConfidentialClientApplication(
            AAD_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{AAD_TENANT_ID}",
            client_credential=AAD_CLIENT_SECRET
        )
        token_response = app.acquire_token_for_client(
            scopes=["https://analysis.windows.net/powerbi/api/.default"]
        )
        if "access_token" in token_response:
            return token_response["access_token"], None
        else:
            return None, f"Token error: {token_response.get('error_description', 'Unknown error')}"
    except Exception as e:
        return None, f"Failed to get Power BI token: {str(e)}"


def get_embed_token_for_report():
    access_token, err = get_powerbi_access_token()
    if err:
        return None, None, err
    
    if not all([POWERBI_WORKSPACE_ID, POWERBI_REPORT_ID]):
        return None, None, "Missing POWERBI_WORKSPACE_ID or POWERBI_REPORT_ID in .env"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/reports/{POWERBI_REPORT_ID}"
        resp = session.get(report_url, headers=headers, timeout=DEFAULT_TIMEOUT)
        
        if resp.status_code == 401:
            return None, None, "401 Unauthorized. Check service principal permissions."
        elif resp.status_code == 403:
            return None, None, "403 Forbidden. Service principal needs workspace access."
        elif resp.status_code == 404:
            return None, None, f"404 Report not found.\n- Workspace ID: {POWERBI_WORKSPACE_ID}\n- Report ID: {POWERBI_REPORT_ID}"
        
        resp.raise_for_status()
        report_info = resp.json()
        embed_url = report_info.get("embedUrl")
        
        token_url = f"{report_url}/GenerateToken"
        token_body = {"accessLevel": "View"}
        resp = session.post(token_url, headers=headers, json=token_body, timeout=DEFAULT_TIMEOUT)
        
        if resp.status_code == 401:
            return None, None, "401 on GenerateToken. Service principal lacks permission to generate tokens."
        elif resp.status_code == 403:
            return None, None, "403 on GenerateToken. Check tenant settings and workspace permissions."
        
        resp.raise_for_status()
        token_info = resp.json()
        embed_token = token_info.get("token")
        
        return embed_url, embed_token, None
    except requests.exceptions.RequestException as e:
        return None, None, f"Power BI API error: {str(e)}"
    except Exception as e:
        return None, None, f"Error generating embed token: {str(e)}"


def get_powerbi_dataset_summary():
    access_token, err = get_powerbi_access_token()
    if err:
        return None, err
    
    if not all([POWERBI_WORKSPACE_ID, POWERBI_DATASET_ID]):
        return None, "Missing Power BI configuration"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    try:
        url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/datasets/{POWERBI_DATASET_ID}"
        resp = session.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        
        if resp.status_code in [401, 403, 404]:
            return None, f"Dataset API error {resp.status_code}"
        
        resp.raise_for_status()
        dataset_info = resp.json()
        
        tables = []
        tables_url = f"{url}/tables"
        resp = session.get(tables_url, headers=headers, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            tables = [t["name"] for t in resp.json().get("value", [])]
        
        summary = f"""Power BI Dataset: {dataset_info.get('name', 'Unknown')}
Tables: {', '.join(tables) if tables else 'Schema not accessible'}

Note: This is an airline flight dataset containing information about flights, cancellations, delays, and related metrics."""
        return summary, None
    except Exception as e:
        return None, f"Error fetching dataset: {str(e)}"


def extract_text_from_report_image(image):
    try:
        reader = easyocr.Reader(['en'], gpu=False)
        if isinstance(image, Image.Image):
            image_array = np.array(image)
        else:
            image_array = np.array(Image.open(io.BytesIO(image)))
        results = reader.readtext(image_array)
        extracted_text = "\n".join([text[1] for text in results if text[2] > 0.3])
        return extracted_text, None
    except Exception as e:
        return None, f"OCR failed: {str(e)}"


def capture_screen_ocr():
    try:
        screenshot = pyautogui.screenshot()
        return extract_text_from_report_image(screenshot)
    except Exception as e:
        return None, f"Capture failed: {str(e)}"


def get_dataset_context_with_data():
    summary, err = get_powerbi_dataset_summary()
    if err:
        return None, err
    return summary, None


def render_powerbi_embed(embed_url, embed_token, report_id):
    html = f"""
    <div id="reportContainer" style="width:100%; height:600px;"></div>
    <script src="https://cdn.jsdelivr.net/npm/powerbi-client@2.22.3/dist/powerbi.min.js"></script>
    <script>
        var models = window['powerbi-client'].models;
        var embedConfiguration = {{
            type: 'report',
            id: '{report_id}',
            embedUrl: '{embed_url}',
            accessToken: '{embed_token}',
            tokenType: models.TokenType.Embed,
            settings: {{
                panes: {{
                    filters: {{ visible: false }},
                    pageNavigation: {{ visible: true }}
                }}
            }}
        }};
        var reportContainer = document.getElementById('reportContainer');
        var report = powerbi.embed(reportContainer, embedConfiguration);
    </script>
    """
    return html


st.set_page_config(layout="wide", page_title="Power BI Insights", initial_sidebar_state="expanded")

if "ocr_data" not in st.session_state:
    st.session_state.ocr_data = None

missing_config = []
if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]):
    missing_config.append("Azure AD (AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET)")
if not all([POWERBI_WORKSPACE_ID, POWERBI_REPORT_ID]):
    missing_config.append("Power BI (POWERBI_WORKSPACE_ID, POWERBI_REPORT_ID)")

if missing_config:
    st.error(f"⚠️ Missing configuration: {', '.join(missing_config)}")
    st.markdown("Please check your `.env` file.")
    st.stop()

left, right = st.columns([3, 1])

with left:
    st.markdown("### Power BI Report")
    
    embed_url, embed_token, err = get_embed_token_for_report()
    
    if err:
        st.error(err)
    else:
        embed_html = render_powerbi_embed(embed_url, embed_token, POWERBI_REPORT_ID)
        st.components.v1.html(embed_html, height=620)
        st.session_state.embed_url = embed_url
        st.session_state.embed_token = embed_token

@st.fragment
def insights_panel():
    st.header("Prompt:")
    prompt = st.text_area("Enter your prompt here", height=100, label_visibility="collapsed", key="prompt_box")
    generate_btn = st.button("Generate Insights")
    
    st.markdown("### Insights:")

    if generate_btn and prompt:
        with st.spinner("🔍 Capturing screen & generating insights..."):
            try:
                ocr_data, ocr_err = capture_screen_ocr()
                if ocr_err:
                    st.warning(f"OCR: {ocr_err}")
                
                report_text, err = get_dataset_context_with_data()

                messages = [
                    {"role": "system", "content": """You are analyzing a Power BI airline flight dashboard. The OCR data contains text extracted from the screen.

DASHBOARD COMPONENTS:
1.  Cards (top section):
   - Total Flights (e.g., "6M" = 6 million flights)
   - Avg Delay (in minutes)
   - Cancellation Rate (percentage)

2. Delay Breakdown (donut/pie chart):
   - Shows delay types: Air System Delays, Airline Delays, Late Aircraft Delays, Security Delays, Weather Delays
   - Values shown as "X.XXM (XX.XX%)" format

3. Cancellations Over Time (bar chart):
   - X-axis: Months (January through December)
   - Y-axis: Sum of Cancelled flights (in thousands, "K")
   - Shows monthly cancellation trends

INTERPRETING VALUES:
- "M" = millions, "K" = thousands
- Percentages in parentheses show proportion of total
- Bar heights indicate relative values across months

When answering questions:
- Reference specific chart data when relevant
- For trends, describe patterns (increasing, decreasing, peaks, etc.)
- Identify which month has highest/lowest values when asked
- Give direct, specific answers using exact numbers from the dashboard
- Ignore browser UI text and noise in the OCR data."""}
                ]
                if report_text:
                    messages.append({"role": "system", "content": f"Dataset Context:\n{report_text}"})
                if ocr_data:
                    messages.append({"role": "system", "content": f"Extracted Data:\n{ocr_data}"})
                messages.append({"role": "user", "content": prompt})

                response = client.chat.completions.create(
                    model=deployment_name,
                    messages=messages,
                    max_tokens=300
                )
                summary = response.choices[0].message.content.strip()
                st.success("Generated successfully!")
                st.write(summary)
            except Exception as e:
                st.error(f"Error: {str(e)}")

with right:
    insights_panel()