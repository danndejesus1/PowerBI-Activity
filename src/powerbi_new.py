import streamlit as st
import os
import requests
from dotenv import load_dotenv
from openai import AzureOpenAI
import msal
import json
import hashlib
import time
from datetime import datetime

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
POWERBI_DATASET_ID = os.getenv("POWERBI_DATASET_ID")

# ============================================================================
# Helper Functions
# ============================================================================

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
        return None, None, f"Failed to get embed token: {str(e)}"

def execute_dax_query(dax_query):
    """Execute a DAX query against Power BI dataset"""
    try:
        access_token, err = get_powerbi_access_token()
        if err:
            return None, err
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/datasets/{POWERBI_DATASET_ID}/executeQueries"
        
        payload = {
            "queries": [{"query": dax_query}],
            "serializationSettings": {"includeNulls": True}
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        
        if response.status_code == 200:
            return response.json(), None
        else:
            return None, f"DAX query failed: {response.status_code} - {response.text[:200]}"
    except Exception as e:
        return None, f"Error executing DAX: {str(e)}"

# ============================================================================
# Page Setup
# ============================================================================

st.set_page_config(layout="wide", page_title="Power BI Insights", initial_sidebar_state="expanded")

# Initialize session state
if 'detected_filters' not in st.session_state:
    st.session_state.detected_filters = {}
if 'last_filter_hash' not in st.session_state:
    st.session_state.last_filter_hash = ""
if 'insights_history' not in st.session_state:
    st.session_state.insights_history = []

# Check for missing config
missing_config = []
if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]):
    missing_config.append("Azure AD credentials")
if not all([POWERBI_WORKSPACE_ID, POWERBI_REPORT_ID, POWERBI_DATASET_ID]):
    missing_config.append("Power BI IDs")

if missing_config:
    st.error(f"❌ Missing: {', '.join(missing_config)}")
    st.stop()

# ============================================================================
# Main UI Layout
# ============================================================================

st.title("🚀 Power BI + AI Insights")

left, right = st.columns([3, 1], gap="medium")

# LEFT COLUMN: Power BI Report
with left:
    st.subheader("Power BI Report")
    
    embed_url, embed_token, err = get_embed_token_for_report()
    if err:
        st.error(f"❌ {err}")
    else:
        # Power BI embed with filter detection
        embed_html = f"""
        <div id="reportContainer" style="width:100%; height:700px; border: 1px solid #ddd; border-radius: 8px;"></div>
        <script src="https://cdn.jsdelivr.net/npm/powerbi-client@2.22.3/dist/powerbi.min.js"></script>
        <script>
            var models = window['powerbi-client'].models;
            var embedConfiguration = {{
                type: 'report',
                id: '{POWERBI_REPORT_ID}',
                embedUrl: '{embed_url}',
                accessToken: '{embed_token}',
                tokenType: models.TokenType.Embed,
                settings: {{
                    panes: {{
                        filters: {{visible: false}},
                        pageNavigation: {{visible: true}}
                    }},
                    localeSettings: {{ locale: 'en' }}
                }},
                permissions: models.Permissions.All
            }};
            
            var reportContainer = document.getElementById('reportContainer');
            var report = powerbi.embed(reportContainer, embedConfiguration);
            
            // Store last filter state
            var lastFilterState = null;
            
            report.on('loaded', function() {{
                console.log('[PowerBI] Report loaded successfully');
            }});
            
            report.on('error', function(event) {{
                console.error('[PowerBI] Report error:', event.detail);
            }});
            
            // Listen for filter changes
            report.on('filtersApplied', async function(event) {{
                try {{
                    var page = await report.getActivePage();
                    var filters = await page.getFilters();
                    
                    var parsedFilters = {{}};
                    
                    if (filters && Array.isArray(filters)) {{
                        filters.forEach(function(filter) {{
                            try {{
                                if (filter.target && filter.target.column) {{
                                    var colName = filter.target.column;
                                    var tableName = filter.target.table || 'Unknown';
                                    
                                    var values = [];
                                    if (Array.isArray(filter.values)) {{
                                        values = filter.values;
                                    }}
                                    
                                    parsedFilters[colName] = {{
                                        table: tableName,
                                        values: values
                                    }};
                                }}
                            }} catch (e) {{
                                console.log('Filter parse error:', e);
                            }}
                        }});
                    }}
                    
                    var currentState = JSON.stringify(parsedFilters);
                    
                    // Send to parent via global variable (for polling)
                    window.powerbiFilters = parsedFilters;
                    window.powerbiFiltersJSON = currentState;
                    window.powerbiFiltersTimestamp = Date.now();
                    
                    // Also post message to ensure parent gets it
                    try {{
                        window.parent.postMessage({{
                            source: 'powerbi-embed',
                            type: 'filters-updated',
                            filters: parsedFilters,
                            timestamp: Date.now()
                        }}, '*');
                    }} catch (e) {{
                        console.log('PostMessage error:', e);
                    }}
                    
                    console.log('[PowerBI] Filters updated:', parsedFilters);
                }} catch (error) {{
                    console.error('[PowerBI] Error getting filters:', error);
                }}
            }});
            
            // Periodic polling to detect filter changes (fallback)
            setInterval(async function() {{
                try {{
                    var page = await report.getActivePage();
                    var filters = await page.getFilters();
                    
                    var newState = JSON.stringify(filters);
                    if (newState !== lastFilterState) {{
                        lastFilterState = newState;
                        console.log('[PowerBI Polling] Filter state changed');
                    }}
                }} catch (e) {{
                    // Silently ignore polling errors
                }}
            }}, 3000);
        </script>
        """
        st.components.v1.html(embed_html, height=750)

# RIGHT COLUMN: Insights Panel
with right:
    st.subheader("💡 AI Insights")
    
    # JavaScript listener for filter messages
    listener_script = """
    <script>
        console.log('[Listener] Script loaded');
        
        // Listen for postMessage events from Power BI iframe
        window.addEventListener('message', function(event) {{
            try {{
                if (event.data && event.data.type === 'filters-updated') {{
                    console.log('[Listener] Received filters via postMessage:', event.data.filters);
                    window.detectedPowerBIFilters = event.data.filters;
                    window.filterDetectionTime = Date.now();
                }}
            }} catch (e) {{
                console.error('[Listener] Error:', e);
            }}
        }});
        
        // Poll the global variable set by iframe
        setInterval(function() {{
            try {{
                if (window.powerbiFilters) {{
                    console.log('[Listener] Found powerbi filters:', window.powerbiFilters);
                    window.detectedPowerBIFilters = window.powerbiFilters;
                    window.filterDetectionTime = window.powerbiFiltersTimestamp;
                }}
            }} catch (e) {{
                console.log('[Listener] Poll error:', e);
            }}
        }}, 1000);
    </script>
    """
    st.components.v1.html(listener_script, height=0)
    
    # Hidden text input that updates based on detected filters (for Streamlit rerun)
    detected_filters_json = st.text_input(
        "Detected Filters",
        key="detected_filters_json",
        value="",
        label_visibility="collapsed"
    )
    
    # Check if filters changed
    if detected_filters_json:
        try:
            new_filters = json.loads(detected_filters_json) if detected_filters_json else {}
            new_hash = hashlib.md5(json.dumps(new_filters, sort_keys=True).encode()).hexdigest()
            
            if new_hash != st.session_state.last_filter_hash:
                st.session_state.last_filter_hash = new_hash
                st.session_state.detected_filters = new_filters
                st.rerun()
        except:
            pass
    
    # Display filter status
    if st.session_state.detected_filters:
        with st.container(border=True):
            st.markdown("### 🎯 Current Filters")
            for col, info in st.session_state.detected_filters.items():
                if isinstance(info, dict) and 'values' in info:
                    values = info['values']
                    if values:
                        values_str = ", ".join([str(v) for v in values[:3]])
                        if len(values) > 3:
                            values_str += f", +{len(values)-3} more"
                        st.write(f"**{col}**: {values_str}")
    else:
        st.info("👆 Apply filters in Power BI above to see insights")
    
    st.divider()
    
    # Generate insights button
    if st.button("🔍 Generate Insights", use_container_width=True):
        if st.session_state.detected_filters:
            with st.spinner("📊 Analyzing data..."):
                try:
                    # Build filter conditions for DAX
                    filter_conditions = []
                    for col, info in st.session_state.detected_filters.items():
                        if isinstance(info, dict) and info.get('values'):
                            values = info['values']
                            if values:
                                # Create DAX IN condition
                                values_str = ", ".join([f'"{v}"' if isinstance(v, str) else str(v) for v in values])
                                filter_conditions.append(f"'{col}' IN {{{values_str}}}")
                    
                    filter_clause = " && ".join(filter_conditions) if filter_conditions else ""
                    
                    # Execute DAX queries
                    stats = {}
                    
                    # Total flights
                    if filter_clause:
                        query = f"EVALUATE {{ROW(\"Total\", COUNTROWS(FILTER('flights', {filter_clause})))}}"
                    else:
                        query = "EVALUATE {ROW(\"Total\", COUNTROWS('flights'))}"
                    
                    result, err = execute_dax_query(query)
                    if not err and result:
                        try:
                            total = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [{}])[0]
                            stats['total'] = list(total.values())[0] if total else 0
                        except:
                            stats['total'] = 0
                    
                    st.info(f"✅ Generated insight based on {stats.get('total', '?')} flights")
                
                except Exception as e:
                    st.error(f"❌ Error: {str(e)}")
        else:
            st.warning("⚠️ No filters detected yet")
    
    st.divider()
    st.markdown("### 📜 Recent Insights")
    if st.session_state.insights_history:
        for i, insight in enumerate(st.session_state.insights_history[-5:], 1):
            with st.expander(f"{i}. {insight['time']}"):
                st.write(insight['text'])
    else:
        st.caption("No insights generated yet")
