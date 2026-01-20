"""
DAX Query Agent Setup - Enhanced with Auto-Insights
Handles LLM agent initialization, DAX documentation, query tools, and contextual insights
"""
import os
import json
import requests
import time
from dotenv import load_dotenv
from openai import AzureOpenAI
import streamlit as st

from langchain_openai import AzureChatOpenAI
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage

load_dotenv()

_dax_docs_cache = None

def load_dax_documentation():
    """Load DAX documentation from text file"""
    global _dax_docs_cache
    
    if _dax_docs_cache is not None:
        return _dax_docs_cache
    
    try:

        doc_paths = [
            'dax_documentation.txt',
            os.path.join(os.path.dirname(__file__), '..', 'dax_documentation.txt'),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'dax_documentation.txt')
        ]
        
        for path in doc_paths:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    _dax_docs_cache = f.read()
                    return _dax_docs_cache
        
        return "No documentation available. Please create dax_documentation.txt"
    except Exception as e:
        return f"Error loading documentation: {str(e)}"

def get_relevant_dax_docs(query: str) -> str:
    """
    Get relevant DAX documentation based on keywords.
    Simple text-based retrieval without vector embeddings.
    """
    full_docs = load_dax_documentation()
    query_lower = query.lower()
    
    sections = full_docs.split('\n## ')
    relevant = []

    if any(word in query_lower for word in ['delay', 'breakdown', 'compare', 'types', 'categories']):
        relevant.extend([s for s in sections if 'UNION' in s or 'compare multiple' in s.lower()])
    
    if any(word in query_lower for word in ['top', 'highest', 'lowest', 'best', 'worst', 'most', 'least']):
        relevant.extend([s for s in sections if 'TOPN' in s])
    
    if any(word in query_lower for word in ['group', 'by', 'each', 'per']):
        relevant.extend([s for s in sections if 'ADDCOLUMNS' in s or 'Group' in s])
    
    if any(word in query_lower for word in ['count', 'how many', 'total number']):
        relevant.extend([s for s in sections if 'COUNTROWS' in s or 'Count' in s])
    
    if any(word in query_lower for word in ['sum', 'total', 'amount']):
        relevant.extend([s for s in sections if 'SUM' in s])
    
    # For auto-insights: add quick query patterns
    if any(word in query_lower for word in ['insight', 'summary', 'overview', 'quick']):
        relevant.extend([s for s in sections if 'Common Patterns' in s or 'Quick Stats' in s])

    relevant.extend([s for s in sections if 'Critical Rules' in s])
    
    if not relevant:
        important_sections = ['EVALUATE', 'ADDCOLUMNS', 'Common Patterns', 'Critical Rules']
        relevant = [s for s in sections if any(imp in s for imp in important_sections)]
    
    unique_sections = list(dict.fromkeys(relevant))
    return '\n\n## '.join(unique_sections[:5])

AAD_TENANT_ID = os.getenv("AAD_TENANT_ID")
AAD_CLIENT_ID = os.getenv("AAD_CLIENT_ID")
AAD_CLIENT_SECRET = os.getenv("AAD_CLIENT_SECRET")
POWERBI_WORKSPACE_ID = os.getenv("POWERBI_WORKSPACE_ID")
POWERBI_DATASET_ID = os.getenv("POWERBI_DATASET_ID")

def get_powerbi_access_token():
    if not all([AAD_TENANT_ID, AAD_CLIENT_ID, AAD_CLIENT_SECRET]):
        return None, "Missing Azure AD credentials"
    try:
        import msal
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

def discover_table_columns():
    """Discover actual column names from Power BI dataset"""
    def execute_dax_query(dax_query):
        access_token, err = get_powerbi_access_token()
        if err:
            return None, err
        if not all([POWERBI_WORKSPACE_ID, POWERBI_DATASET_ID]):
            return None, "Missing POWERBI_WORKSPACE_ID or POWERBI_DATASET_ID"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/datasets/{POWERBI_DATASET_ID}/executeQueries"
        payload = {
            "queries": [{"query": dax_query}],
            "serializerSettings": {"includeNulls": True}
        }
        
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code != 200:
                return None, f"Error {resp.status_code}"
            return resp.json(), None
        except Exception as e:
            return None, f"Query failed: {str(e)}"
    
    tables = ['flights', 'airlines', 'origin_airport', 'destination_airport']
    schema = {}
    
    for table in tables:
        query = f"EVALUATE TOPN(1, '{table}')"
        result, err = execute_dax_query(query)
        if not err and result:
            try:
                rows = result.get("results", [{}])[0].get("tables", [{}])[0].get("rows", [])
                if rows:
                    columns = list(rows[0].keys())
                    clean_columns = []
                    for col in columns:
                        if '[' in col and ']' in col:
                            clean_col = col.split('[')[1].rstrip(']')
                            clean_columns.append(clean_col)
                        else:
                            clean_columns.append(col)
                    schema[table] = clean_columns
            except:
                schema[table] = []
    
    return schema

_schema_cache = None
_schema_cache_time = None

def get_cached_schema():
    """Get cached schema with simple time-based invalidation"""
    global _schema_cache, _schema_cache_time
    
    current_time = time.time()
    
  
    if _schema_cache is not None and _schema_cache_time is not None:
        if current_time - _schema_cache_time < 3600:
            return _schema_cache
    
  
    _schema_cache = discover_table_columns()
    _schema_cache_time = current_time
    return _schema_cache

def execute_dax_query(dax_query):
    """Execute a DAX query against Power BI dataset"""
    access_token, err = get_powerbi_access_token()
    if err:
        return None, err
    if not all([POWERBI_WORKSPACE_ID, POWERBI_DATASET_ID]):
        return None, "Missing POWERBI_WORKSPACE_ID or POWERBI_DATASET_ID"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{POWERBI_WORKSPACE_ID}/datasets/{POWERBI_DATASET_ID}/executeQueries"
    payload = {
        "queries": [{"query": dax_query}],
        "serializerSettings": {"includeNulls": True}
    }
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 400:
            error_detail = resp.json().get('error', {})
            error_msg = error_detail.get('message', 'Invalid query')
            error_code = error_detail.get('code', '')
            return None, f"DAX query error ({error_code}): {error_msg}"
        elif resp.status_code == 401:
            return None, "401 Unauthorized"
        elif resp.status_code == 403:
            return None, "403 Forbidden"
        elif resp.status_code == 404:
            return None, "404 Not Found"
        resp.raise_for_status()
        return resp.json(), None
    except Exception as e:
        return None, f"DAX query failed: {str(e)}"

def format_dax_results(dax_response):
    """Format DAX query results for display"""
    if not dax_response or "results" not in dax_response:
        return "No data returned"
    formatted = []
    for result in dax_response.get("results", []):
        if "tables" in result:
            for table in result["tables"]:
                rows = table.get("rows", [])
                if rows:
                    formatted.append(json.dumps(rows, indent=2))
    return "\n\n".join(formatted) if formatted else "No data found"

@tool
def dax_syntax_tool(query: str) -> str:
    """
    Get DAX syntax documentation for functions.
    Input: keywords from the user's question like 'delay breakdown', 'top values', 'grouping', etc.
    """
    return get_relevant_dax_docs(query)

@tool
def schema_tool(input: str = "") -> str:
    """
    Get Power BI dataset schema with all tables and columns.
    Use this first to know what data is available.
    """
    try:
        schema = get_cached_schema()
        result = "Power BI Dataset Schema:\n\n"
        for table, columns in schema.items():
            result += f"Table: '{table}'\nColumns: {', '.join([f'[{col}]' for col in columns])}\n\n"
        return result
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def execute_dax_tool(dax_query: str) -> str:
    """
    Execute a DAX query against Power BI dataset.
    Input: Complete valid DAX query starting with EVALUATE.
    Only use after constructing a valid query.
    """
    result, err = execute_dax_query(dax_query)
    if err:
        return f"Error: {err}"
    return format_dax_results(result)

@tool
def quick_stats_tool(metric: str) -> str:
    """
    Get quick statistics for common metrics without writing full DAX.
    Input: metric name like 'total_flights', 'avg_delay', 'cancellation_rate'
    Useful for fast auto-insights.
    """
    metric_lower = metric.lower()
    
   
    queries = {
        'total_flights': "EVALUATE ROW(\"Total Flights\", COUNTROWS('flights'))",
        'avg_delay': "EVALUATE ROW(\"Avg Delay\", AVERAGE('flights'[DEPARTURE_DELAY]))",
        'total_delays': "EVALUATE ROW(\"Total Delays\", SUM('flights'[DEPARTURE_DELAY]))",
        'cancellation_rate': """EVALUATE 
            ROW(
                "Cancellation Rate", 
                DIVIDE(COUNTROWS(FILTER('flights', 'flights'[CANCELLED] = 1)), COUNTROWS('flights'))
            )""",
        'cancelled_count': "EVALUATE ROW(\"Cancelled\", COUNTROWS(FILTER('flights', 'flights'[CANCELLED] = 1)))"
    }
    
    # Find matching query
    for key, query in queries.items():
        if key in metric_lower or metric_lower in key:
            result, err = execute_dax_query(query)
            if err:
                return f"Error: {err}"
            return format_dax_results(result)
    
    return f"Unknown metric: {metric}. Available: {', '.join(queries.keys())}"

@tool
def compare_tool(dimension: str, top_n: int = 5) -> str:
    """
    Compare values across a dimension (like airlines, airports, months).
    Input: dimension name ('airline', 'origin', 'desti  nation', 'month') and optional top_n (default 5)
    Returns top performers for that dimension.
    """
    dimension_lower = dimension.lower()
    
    # Map dimensions to queries
    if 'airline' in dimension_lower:
        query = f"""EVALUATE
            TOPN(
                {top_n},
                ADDCOLUMNS(
                    VALUES('airlines'[AIRLINE]),
                    "Total Flights", CALCULATE(COUNTROWS('flights')),
                    "Avg Delay", CALCULATE(AVERAGE('flights'[DEPARTURE_DELAY]))
                ),
                [Total Flights], DESC
            )"""
    elif 'origin' in dimension_lower:
        query = f"""EVALUATE
            TOPN(
                {top_n},
                ADDCOLUMNS(
                    VALUES('origin_airport'[AIRPORT]),
                    "Total Flights", CALCULATE(COUNTROWS('flights'))
                ),
                [Total Flights], DESC
            )"""
    elif 'month' in dimension_lower:
        query = f"""EVALUATE
            TOPN(
                {top_n},
                ADDCOLUMNS(
                    VALUES('flights'[MONTH]),
                    "Total Flights", CALCULATE(COUNTROWS('flights')),
                    "Cancellations", CALCULATE(COUNTROWS(FILTER('flights', 'flights'[CANCELLED] = 1)))
                ),
                [Total Flights], DESC
            )"""
    else:
        return f"Unknown dimension: {dimension}. Available: airline, origin, destination, month"
    
    result, err = execute_dax_query(query)
    if err:
        return f"Error: {err}"
    return format_dax_results(result)

SYSTEM_PROMPT = """You are a DAX query expert for Power BI flight data analysis.

Your workflow:
1. Use schema_tool to see available tables and columns
2. Use dax_syntax_tool with keywords from the user's question to get relevant DAX documentation
3. Construct a valid DAX query using exact column names from schema
4. Use execute_dax_tool to run the query
5. Interpret results and answer the user's question

ADDITIONAL TOOLS FOR AUTO-INSIGHTS:
- quick_stats_tool: Get common metrics instantly (total flights, avg delay, etc.)
- compare_tool: Compare across dimensions (airlines, airports, months)

FOR AUTO-GENERATED INSIGHTS (when user changes filters):
- Be extremely concise (2-3 sentences max)
- Use quick_stats_tool for fast metrics when possible
- Focus on the most important finding
- Include specific numbers
- Compare to baseline when relevant
- Don't explain methodology, just provide insight

FOR MANUAL QUESTIONS:
- Be thorough and detailed
- Show DAX queries when helpful
- Explain reasoning

CRITICAL RULES:
- Always check schema first for exact column names
- Look up DAX syntax for functions you need
- Use ADDCOLUMNS + VALUES + CALCULATE for grouping (NOT SUMMARIZE with aggregations)
- For delay/cancellation totals: use SUM() not COUNTROWS()
- All queries must start with EVALUATE
- Match column names EXACTLY as shown in schema
- For delay breakdowns, use UNION pattern with ROW for each delay type

Be methodical: check schema → lookup syntax → build query → execute → interpret."""

def create_dax_agent():
    """Create and configure the DAX query agent"""
    
    # LangChain LLM
    llm = AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        deployment_name=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"),
        temperature=0.1
    )
    
    # Define tools list for the agent - now includes quick tools
    tools = [
        dax_syntax_tool, 
        schema_tool, 
        execute_dax_tool,
        quick_stats_tool,  # NEW: Fast stats for auto-insights
        compare_tool       # NEW: Quick comparisons
    ]
    
    # Create agent
    agent = create_react_agent(llm, tools)
    
    # Return both agent and system prompt
    return agent, SYSTEM_PROMPT