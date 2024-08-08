import streamlit as st
import openai
from openai import OpenAI
import pandas as pd
from PIL import Image
import io
import base64
from PyPDF2 import PdfReader
import re
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import json
import os
from dotenv import load_dotenv

# Load environment variables (for local development without secrets.toml)
load_dotenv()

# OpenAI setup
api_key = st.secrets["openai"]["api_key"] if "openai" in st.secrets else os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

MAX_TOKENS = 128000

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

if 'google_sheets' in st.secrets:
    # Remove any whitespace and newline characters from the credentials string
    credentials_str = ''.join(st.secrets["google_sheets"]["credentials"].split())
    try:
        credentials_dict = json.loads(credentials_str)
    except json.JSONDecodeError:
        st.error("Error decoding Google Sheets credentials. Please check your secrets.toml file.")
        credentials_dict = None
    SPREADSHEET_ID = st.secrets["google_sheets"]["spreadsheet_id"]
    RANGE_NAME = st.secrets["google_sheets"]["range_name"]
else:
    # Fallback to environment variables
    credentials_str = os.getenv('GOOGLE_SHEETS_CREDENTIALS', '{}')
    try:
        credentials_dict = json.loads(credentials_str)
    except json.JSONDecodeError:
        st.error("Error decoding Google Sheets credentials from environment variable.")
        credentials_dict = None
    SPREADSHEET_ID = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
    RANGE_NAME = os.getenv('GOOGLE_SHEETS_RANGE_NAME')

if credentials_dict:
    creds = Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)
    sheets_service = build('sheets', 'v4', credentials=creds)
else:
    st.warning("Google Sheets integration is not available due to credential issues.")
    sheets_service = None

def extract_text_with_openai(file):
    pdf_reader = PdfReader(file)
    texts = []
    
    for page in pdf_reader.pages:
        page_content = page.extract_text()
        
        page_content_base64 = base64.b64encode(page_content.encode('utf-8')).decode('utf-8')
        truncated_content = truncate_content(page_content_base64, MAX_TOKENS)
        prompt = f"Extract the text from the following file content (base64-encoded): {truncated_content}"
        
        response = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": "You are an OCR engine."},
                {"role": "user", "content": prompt}
            ]
        )
        
        texts.append(response.choices[0].message.content)
    
    return texts

def truncate_content(content, max_tokens):
    estimated_token_length = len(content) // 4
    if estimated_token_length > max_tokens:
        return content[:max_tokens * 4]
    return content

def parse_text_with_gpt(text):
    response = client.chat.completions.create(
        model="gpt-4-turbo",
        messages=[
            {"role": "system", "content": "You are a solar quote comparison tool used to compare solar quotes for price per watt, system specs and more. Present the information in a clear, readable format. Ensure all calculations are accurate, especially the cost per watt and total system price."},
            {"role": "user", "content": f"""
            Extract and present the following details from the text in a clear, readable format. Be concise in your analysis and make it human-readable. It is absolutely essential that the numbers you pull from the PDF are accurate and that the math for cost per watt and total system price is correct. Double check all calculations before outputting.

            1. Extract the following information:
            - Installer Name
            - Installer Email
            - Installer Phone
            - Total Price (in USD)
            - System Size (in kW)
            - Estimated Annual Production (kWh)
            - Panel Information
            - Inverter Model and Output
            - Incentives or Rebates
            - Warranty Information
            - Estimated Payback Period
            - Customer Email
            - Customer Phone Number

            2. Calculate and verify the Cost per Watt:
            - Divide the Total Price by (System Size in kW * 1000)
            - Round the result to 2 decimal places
            - Double-check this calculation to ensure accuracy
            - Include this verified calculation in your output

            3. Verify the Total System Price:
            - Ensure that the Total Price matches any breakdown provided in the quote
            - If there's a discrepancy, note it and use the most accurate figure

            4. Provide a brief, concise analysis of the quote (3-4 sentences max), including:
            - How this quote compares to typical market rates
            - A key recommendation for the customer

            Format your response as follows:
            Installer Name: [Value]
            Installer Email: [Value]
            Installer Phone: [Value]
            Total Price: [Value]
            System Size: [Value]
            Cost per Watt: [Calculated and Verified Value]
            Estimated Annual Production: [Value]
            Panel Information: [Value]
            Inverter Model and Output: [Value]
            Incentives or Rebates: [Value]
            Warranty Information: [Value]
            Estimated Payback Period: [Value]
            Customer Email: [Value]
            Customer Phone Number: [Value]

            Analysis: [Your brief analysis]

            Here is the text: {text}
            """
            }
        ]
    )
    return response.choices[0].message.content

def process_gpt_output(output):
    parts = output.split("\n\nAnalysis:", 1)
    data_part = parts[0]
    analysis_part = parts[1] if len(parts) > 1 else ""

    data_lines = data_part.split('\n')
    data_dict = {}
    display_dict = {}
    for line in data_lines:
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            data_dict[key] = value
            if key not in ['Customer Email', 'Customer Phone Number']:
                display_dict[key] = value

    df_full = pd.DataFrame([data_dict])
    df_display = pd.DataFrame([display_dict])

    return df_full, df_display, analysis_part.strip()

def add_to_google_sheet(data):
    try:
        sheet = sheets_service.spreadsheets()
        values = [[
            data.get('Installer Name', ''),
            data.get('Installer Email', ''),
            data.get('Installer Phone', ''),
            data.get('Total Price', ''),
            data.get('System Size', ''),
            data.get('Cost per Watt', ''),
            data.get('Estimated Annual Production', ''),
            data.get('Panel Information', ''),
            data.get('Inverter Model and Output', ''),
            data.get('Incentives or Rebates', ''),
            data.get('Warranty Information', ''),
            data.get('Estimated Payback Period', ''),
            data.get('Customer Email', ''),
            data.get('Customer Phone Number', '')
        ]]
        body = {'values': values}
        result = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME,
            valueInputOption='USER_ENTERED', body=body).execute()
        return True
    except Exception as e:
        print(f"Error adding data to Google Sheet: {str(e)}")  # Log the error server-side
        return False

def create_solar_terms_glossary():
    terms = {
        "Solar Panel": "A device that converts sunlight into electricity.",
        "Solar Inverter": "Converts DC electricity from solar panels into AC electricity for home use.",
        "Cost per Watt": "The total cost of the solar system divided by its power output in watts.",
        "Payback Period": "The time it takes for energy savings to equal the cost of the solar system.",
        "Annual Production": "The estimated amount of electricity your solar system will generate in a year.",
        "kWh (Kilowatt-hour)": "A unit of energy equal to 1,000 watt-hours, used to measure electricity consumption.",
        "Net Metering": "A billing system that credits solar energy system owners for electricity they add to the grid.",
        "PV (Photovoltaic)": "The conversion of light into electricity using semiconducting materials.",
        "Efficiency": "The percentage of sunlight a solar panel can convert into usable electricity.",
        "Degradation Rate": "The rate at which solar panels lose efficiency over time."
    }
    return pd.DataFrame(list(terms.items()), columns=['Term', 'Definition'])

def main():
    st.title("Solar Quote Comparison Tool")
    st.subheader("Upload your solar quotes to compare prices per watt, system sizes, incentives available and more.")

    st.info("💡 Cost per Watt is a common metric used to compare solar quotes. It's calculated by dividing the total system cost by the system size in watts. Lower cost per watt generally indicates better value, but other factors should also be considered.")
    st.divider()

    uploaded_files = st.file_uploader("Upload PDFs of quotes you've received", type=["pdf"], accept_multiple_files=True)

    if uploaded_files:
        process_button = st.button("Get Insights")
        if process_button:
            for i, uploaded_file in enumerate(uploaded_files):
                with st.container():
                    st.write(f"Processing file {i+1}: {uploaded_file.name}")
                    with st.spinner('Parsing quote for relevant info. This will take a minute...'):
                        try:
                            texts = extract_text_with_openai(uploaded_file)
                            combined_text = "\n".join(texts)

                            parsed_data = parse_text_with_gpt(combined_text)
                            
                            df_full, df_display, analysis_part = process_gpt_output(parsed_data)
                            
                            st.subheader(f"Quote {i+1}: {uploaded_file.name}")
                            
                            st.dataframe(df_display.T.style.set_properties(**{'text-align': 'left'}))
                            
                            st.subheader(f"Analysis of Quote {i+1}")
                            st.markdown(analysis_part)
                            
                            if add_to_google_sheet(df_full.iloc[0].to_dict()):
                                print("Data added to Google Sheet successfully!")
                            else:
                                print("Failed to add data to Google Sheet.")
                            
                            with st.expander(f"View Raw Extracted Data for Quote {i+1}"):
                                st.text("\n".join([f"{k}: {v}" for k, v in df_display.iloc[0].items()]))
                            
                        except Exception as e:
                            st.error(f"Error processing file {uploaded_file.name}: {str(e)}")
                        
                    st.divider()
            
            st.balloons()

            st.subheader("Solar Terms Glossary")
            with st.expander("Click here to view definitions of key solar terms"):
                st.dataframe(create_solar_terms_glossary())

    st.markdown(
        """
        <style>
        .footer {
            position: fixed;
            left: 0;
            bottom: 0;
            width: 100%;
            background-color: white;
            color: black;
            text-align: center;
            padding: 10px;
            border-top: 1px solid #eaeaea;
        }
        </style>
        <div class="footer">
            <p><a href="https://www.currents.com?utm_source=solar-quote" target="_blank">Powered by Currents</a></p>
        </div>
        """,
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()