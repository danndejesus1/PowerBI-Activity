import streamlit as st
import os
import requests
from dotenv import load_dotenv
from openai import AzureOpenAI
import msal

# Import agent setup from separate module
from dax_agent import create_dax_agent

load_dotenv()

# Azure OpenAI setup
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
)

AAD_TENANT_ID = os.getenv("AAD_TENANT_ID")
AAD_CLIENT_ID = os.getenv("AAD_CLIENT_ID")
AAD_CLIENT_SECRET = os.getenv("AAD_CLIENT_SECRET")
POWERBI_WORKSPACE_ID = os.getenv("POWERBI_WORKSPACE_ID")
POWERBI_REPORT_ID = os.getenv("POWERBI_REPORT_ID")

# Create DAX agent and get system prompt
agent_executor, system_prompt = create_dax_agent()

# Helper function for Power BI token
def get_powerbi_access_token():
    if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]):
        return None, "Missing Azure AD credentials"
    try:
        app = msal.ConfidentialClientApplication(
            AAD_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{AAD_TENANT_ID}",
            client_credential=AAD_CLIENT_SECRET
        )
        token_response = app.acquire_token_for_client(scopes=["https://analysis.windows.net/powerbi/api/.default"])
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
        return None, None, "Missing POWERBI_WORKSPACE_ID or POWERBI_REPORT_ID"
    
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    
    try:
        report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/reports/{POWERBI_REPORT_ID}"
        resp = requests.get(report_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return None, None, f"Error {resp.status_code}"
        embed_url = resp.json().get("embedUrl")
        
        token_url = f"{report_url}/GenerateToken"
        resp = requests.post(token_url, headers=headers, json={"accessLevel": "View"}, timeout=30)
        resp.raise_for_status()
        embed_token = resp.json().get("token")
        
        return embed_url, embed_token, None
    except Exception as e:
        return None, None, f"Error: {str(e)}"

def render_powerbi_embed(embed_url, embed_token, report_id):
    return f"""
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
            settings: {{panes: {{filters: {{visible: false}}, pageNavigation: {{visible: true}}}}}}
        }};
        var reportContainer = document.getElementById('reportContainer');
        var report = powerbi.embed(reportContainer, embedConfiguration);
    </script>
    """

# Streamlit UI
st.set_page_config(layout="wide", page_title="Power BI Insights", initial_sidebar_state="expanded")

missing_config = []
if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]):
    missing_config.append("Azure AD credentials")
if not all([POWERBI_WORKSPACE_ID, POWERBI_REPORT_ID]):
    missing_config.append("Power BI IDs")

if missing_config:
    st.error(f"Missing: {', '.join(missing_config)}")
    st.stop()

left, right = st.columns([3, 1])

with left:
    st.markdown("### Power BI Report")
    embed_url, embed_token, err = get_embed_token_for_report()
    if err:
        st.error(err)
    else:
        st.components.v1.html(render_powerbi_embed(embed_url, embed_token, POWERBI_REPORT_ID), height=620)

@st.fragment
def insights_panel():
    st.header("Ask Questions:")
    prompt = st.text_area(
        "Enter your question about the data:", 
        height=100,
        placeholder="e.g., What's the delay breakdown? Which month has lowest cancellations?",
        key="prompt_box"
    )
    generate_btn = st.button("Generate Insights", use_container_width=True)
    
    st.markdown("### Insights:")
    
    if generate_btn and prompt:
        with st.spinner("Agent analyzing..."):
            try:
                from langchain_core.messages import HumanMessage, SystemMessage
                
                # Construct messages with system prompt
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=prompt)
                ]
                
                # Invoke agent
                response = agent_executor.invoke({"messages": messages})
                
                # Extract the final response - handle different response formats
                if response and "messages" in response:
                    final_message = response["messages"][-1]
                    
                    # Check if it's an AIMessage object or dict
                    if hasattr(final_message, 'content'):
                        # It's a LangChain message object
                        output = final_message.content
                    elif isinstance(final_message, dict) and 'content' in final_message:
                        # It's a dictionary
                        output = final_message['content']
                    else:
                        output = str(final_message)
                else:
                    output = str(response)
                
                # Extract DAX queries from tool calls (the actual executed queries)
                dax_queries = []
                for msg in response.get("messages", []):
                    # Check for ToolMessage with execute_dax_tool
                    if hasattr(msg, 'name') and msg.name == 'execute_dax_tool':
                        # This is a tool call message, get the query from previous AIMessage
                        continue
                    
                    # Check for AIMessage with tool_calls
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        for tool_call in msg.tool_calls:
                            if tool_call.get('name') == 'execute_dax_tool':
                                # Extract the DAX query argument
                                args = tool_call.get('args', {})
                                if 'dax_query' in args:
                                    dax_queries.append(args['dax_query'])
                
                # Display DAX Query if found
                if dax_queries:
                    st.markdown("#### 📊 DAX Query")
                    for i, query in enumerate(dax_queries, 1):
                        with st.expander(f"Query {i}", expanded=True):
                            st.code(query, language="sql")
                
                # Display result with better formatting
                st.markdown("#### 💡 Analysis")
                
                # Try to format the output nicely
                if "delay" in output.lower() or "total" in output.lower():
                    # Format numbers with commas
                    import re
                    formatted_output = output
                    # Find large numbers and format them
                    numbers = re.findall(r'\b\d{4,}\b', output)
                    for num in numbers:
                        formatted_num = f"{int(num):,}"
                        formatted_output = formatted_output.replace(num, f"**{formatted_num}**")
                    st.markdown(formatted_output)
                else:
                    st.write(output)
                    
            except Exception as e:
                st.error(f"Error: {str(e)}")
                st.info("Try rephrasing your question or ask something simpler.")
                
                # Show full error details in expander
                with st.expander("Error Details"):
                    import traceback
                    st.code(traceback.format_exc())

with right:
    insights_panel()