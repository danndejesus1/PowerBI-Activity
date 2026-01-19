import streamlit as st
import os
import traceback
from dotenv import load_dotenv

load_dotenv()

st.title("🔍 Configuration Diagnostic")

st.markdown("---")

# Check all environment variables
st.markdown("### 1️⃣ Environment Variables")

config_status = {}

# Azure AD
st.markdown("#### Azure AD")
aad_vars = ["AAD_TENANT_ID", "AAD_CLIENT_ID", "AAD_CLIENT_SECRET"]
for var in aad_vars:
    val = os.getenv(var)
    status = "✅" if val else "❌"
    display_val = "***SET***" if val and "SECRET" in var or "KEY" in var else (val[:20] + "..." if val and len(val) > 20 else val)
    st.write(f"{status} `{var}`: {display_val if val else '**MISSING**'}")
    config_status[var] = bool(val)

st.markdown("#### Azure OpenAI")
openai_vars = ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_DEPLOYMENT_NAME"]
for var in openai_vars:
    val = os.getenv(var)
    status = "✅" if val else "❌"
    display_val = "***SET***" if val and ("SECRET" in var or "KEY" in var) else (val[:50] + "..." if val and len(val) > 50 else val)
    st.write(f"{status} `{var}`: {display_val if val else '**MISSING**'}")
    config_status[var] = bool(val)

st.markdown("#### Power BI")
pbi_vars = ["POWERBI_WORKSPACE_ID", "POWERBI_REPORT_ID", "POWERBI_DATASET_ID"]
for var in pbi_vars:
    val = os.getenv(var)
    status = "✅" if val else "❌"
    st.write(f"{status} `{var}`: {val if val else '**MISSING**'}")
    config_status[var] = bool(val)

st.markdown("---")

# Check files
st.markdown("### 2️⃣ Required Files")
files_to_check = ["dax_documentation.txt", "dax_agent.py", ".env"]
for file in files_to_check:
    exists = os.path.exists(file)
    status = "✅" if exists else "❌"
    st.write(f"{status} `{file}`")
    if exists and file == "dax_documentation.txt":
        with open(file, 'r') as f:
            content = f.read()
            st.write(f"   📄 Size: {len(content)} characters")

st.markdown("---")

# Test Azure OpenAI connection
st.markdown("### 3️⃣ Azure OpenAI Connection Test")

if st.button("Test Azure OpenAI Connection"):
    with st.spinner("Testing..."):
        try:
            from langchain_openai import AzureChatOpenAI
            
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
            api_key = os.getenv("AZURE_OPENAI_API_KEY")
            api_version = os.getenv("AZURE_OPENAI_API_VERSION")
            deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
            
            st.write(f"🔗 Endpoint: `{endpoint}`")
            st.write(f"📦 Deployment: `{deployment}`")
            st.write(f"📋 API Version: `{api_version}`")
            
            llm = AzureChatOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
                deployment_name=deployment,
                temperature=0.1
            )
            
            # Simple test
            response = llm.invoke("Say 'hello' in one word")
            st.success(f"✅ Connection successful!")
            st.write(f"Response: {response.content}")
            
        except Exception as e:
            st.error(f"❌ Connection failed!")
            st.code(str(e))
            with st.expander("Full Error Trace"):
                st.code(traceback.format_exc())

st.markdown("---")

# Test Power BI connection
st.markdown("### 4️⃣ Power BI Connection Test")

if st.button("Test Power BI Connection"):
    with st.spinner("Testing..."):
        try:
            import msal
            import requests
            
            tenant_id = os.getenv("AAD_TENANT_ID")
            client_id = os.getenv("AAD_CLIENT_ID")
            client_secret = os.getenv("AAD_CLIENT_SECRET")
            workspace_id = os.getenv("POWERBI_WORKSPACE_ID")
            dataset_id = os.getenv("POWERBI_DATASET_ID")
            
            # Get token
            app = msal.ConfidentialClientApplication(
                client_id,
                authority=f"https://login.microsoftonline.com/{tenant_id}",
                client_credential=client_secret
            )
            token_response = app.acquire_token_for_client(
                scopes=["https://analysis.windows.net/powerbi/api/.default"]
            )
            
            if "access_token" not in token_response:
                st.error(f"❌ Token acquisition failed!")
                st.code(token_response.get('error_description', 'Unknown error'))
                st.stop()
            
            st.success("✅ Token acquired")
            
            # Test dataset query
            access_token = token_response["access_token"]
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            # Simple test query
            url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{dataset_id}/executeQueries"
            payload = {
                "queries": [{"query": "EVALUATE ROW(\"Test\", 1)"}],
                "serializerSettings": {"includeNulls": True}
            }
            
            st.write(f"🔗 Testing URL: `{url[:80]}...`")
            
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            
            st.write(f"📊 Status Code: {resp.status_code}")
            
            if resp.status_code == 200:
                st.success("✅ Power BI query successful!")
                result = resp.json()
                st.json(result)
            else:
                st.error(f"❌ Query failed with status {resp.status_code}")
                st.code(resp.text)
                
        except Exception as e:
            st.error(f"❌ Test failed!")
            st.code(str(e))
            with st.expander("Full Error Trace"):
                st.code(traceback.format_exc())

st.markdown("---")

# Test agent creation
st.markdown("### 5️⃣ Agent Initialization Test")

if st.button("Test Agent Creation"):
    with st.spinner("Creating agent..."):
        try:
            from dax_agent import create_dax_agent
            
            agent_executor, system_prompt = create_dax_agent()
            
            st.success("✅ Agent created successfully!")
            st.write(f"📝 System prompt length: {len(system_prompt)} characters")
            st.write(f"🤖 Agent type: {type(agent_executor).__name__}")
            
            # Test a simple query
            st.markdown("#### Testing simple query...")
            from langchain_core.messages import HumanMessage, SystemMessage
            
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content="What tables are available?")
            ]
            
            response = agent_executor.invoke({"messages": messages})
            
            st.success("✅ Query executed!")
            
            if response and "messages" in response:
                final_msg = response["messages"][-1]
                if hasattr(final_msg, 'content'):
                    st.write(final_msg.content)
                else:
                    st.json(final_msg)
            
        except Exception as e:
            st.error(f"❌ Agent creation/test failed!")
            st.code(str(e))
            with st.expander("Full Error Trace"):
                st.code(traceback.format_exc())

st.markdown("---")

# Summary
st.markdown("### 📋 Summary")

missing = [k for k, v in config_status.items() if not v]
if missing:
    st.error(f"❌ Missing configuration: {', '.join(missing)}")
else:
    st.success("✅ All environment variables are set!")

st.markdown("---")

st.markdown("""
### 🔧 Common Issues & Fixes

**404 Error - Resource Not Found:**
1. Check `AZURE_OPENAI_DEPLOYMENT_NAME` - must match your actual deployment name
2. Check `AZURE_OPENAI_ENDPOINT` - must end with `/` or not (be consistent)
3. Check `POWERBI_DATASET_ID` - must be the correct GUID
4. Check `POWERBI_WORKSPACE_ID` - must be the correct GUID

**API Version Issues:**
- Use `2024-02-15-preview` for newer features
- Use `2023-05-15` for stable version

**Deployment Name:**
- Must match EXACTLY what you see in Azure OpenAI Studio
- Common names: `gpt-4`, `gpt-35-turbo`, `gpt-4o`
- Case sensitive!
""")