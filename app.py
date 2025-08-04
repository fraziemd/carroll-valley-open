import streamlit as st

# Page configuration must be the first Streamlit command
st.set_page_config(
    page_title="The 'Jimmy D' Carroll Valley Open",
    page_icon="jdcvo.png",
    layout="wide"
)

import pandas as pd
import gspread
from google.oauth2 import service_account
from datetime import datetime
import time
import json

def load_past_champions():
    """
    Load past champions from the players JSON file.
    """
    try:
        with open('players_2024.json', 'r') as f:
            data = json.load(f)
        
        past_champions = []
        for player_id, player_data in data['players'].items():
            if player_data.get('past_champion', False):
                past_champions.append(player_data['name'])
        
        return past_champions
    except Exception as e:
        st.error(f"Error loading past champions: {e}")
        return []

def load_player_round_data(player_name):
    """
    Load round-by-round data for a specific player from Google Sheets.
    """
    try:
        # Set up credentials
        SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
        
        # Try to get credentials from Streamlit secrets first, fall back to file
        if 'gcp_service_account' in st.secrets:
            credentials = service_account.Credentials.from_service_account_info(
                st.secrets['gcp_service_account'], scopes=SCOPES)
        else:
            SERVICE_ACCOUNT_FILE = 'golf-outing-468018-a5bee528ee1c.json'
            credentials = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        
        # Connect to Google Sheets
        gc = gspread.authorize(credentials)
        
        # Open the sheet
        sheet = gc.open_by_key('1qkLn1UmfjTYy76L1rL_G7O-siGcHBolIYdELjTMSFV4')
        
        # Get individual leaderboard which should have round-by-round data
        individual_worksheet = sheet.worksheet('Individual Leaderboard')
        individual_data = individual_worksheet.get_all_records()
        
        # Find the player's row
        player_row = None
        for row in individual_data:
            if row.get('Player') == player_name:
                player_row = row
                break
        
        if not player_row:
            return {}
        
        # Extract round-by-round data using the correct column names
        round_data = {}
        for round_num in [1, 2, 3, 4, 5]:
            round_key = f'R{round_num}'
            if round_key in player_row and player_row[round_key] is not None and player_row[round_key] != '':
                round_data[f'Round {round_num}'] = {
                    'points': player_row[round_key]
                }
        
        # Add Putt-Off and Extras
        if 'Putt-Off' in player_row and player_row['Putt-Off'] is not None and player_row['Putt-Off'] != '':
            round_data['Putt-Off'] = {
                'points': player_row['Putt-Off']
            }
        
        if 'Extras' in player_row and player_row['Extras'] is not None and player_row['Extras'] != '':
            round_data['Extras'] = {
                'points': player_row['Extras']
            }
        
        return round_data
        
    except Exception as e:
        st.error(f"Error loading player round data: {e}")
        return {}

def load_data_from_sheets():
    """
    Load leaderboard data directly from Google Sheets.
    """
    try:
        # Set up credentials
        SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
        
        # Try to get credentials from Streamlit secrets first, fall back to file
        if 'gcp_service_account' in st.secrets:
            credentials = service_account.Credentials.from_service_account_info(
                st.secrets['gcp_service_account'], scopes=SCOPES)
        else:
            SERVICE_ACCOUNT_FILE = 'golf-outing-468018-a5bee528ee1c.json'
            credentials = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        
        # Connect to Google Sheets
        gc = gspread.authorize(credentials)
        
        # Open the sheet
        sheet = gc.open_by_key('1qkLn1UmfjTYy76L1rL_G7O-siGcHBolIYdELjTMSFV4')
        
        # Get individual leaderboard
        individual_worksheet = sheet.worksheet('Individual Leaderboard')
        individual_data = individual_worksheet.get_all_records()
        df_individual = pd.DataFrame(individual_data)
        
        # Get team leaderboard
        team_worksheet = sheet.worksheet('Team Leaderboard')
        team_data = team_worksheet.get_all_records()
        df_team = pd.DataFrame(team_data)
        
        return df_individual, df_team
        
    except Exception as e:
        st.error(f"Error loading data from Google Sheets: {e}")
        return None, None

def main():
    # Add dark theme CSS with custom font
    st.markdown("""
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {
            font-family: 'Inter', sans-serif;
        }
        .stApp {
            background-color: #0E1117;
            color: #FAFAFA;
        }
        .stDataFrame {
            background-color: #262730;
        }
        h1, h2, h3 {
            color: #FAFAFA;
            font-family: 'Inter', sans-serif;
        }
        .stMarkdown {
            color: #FAFAFA;
        }
        /* Dark header/navigation */
        header {
            background-color: #0E1117 !important;
        }
        .stDeployButton {
            background-color: #262730 !important;
        }
        /* Hide the hamburger menu and other header elements */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)
    
    # Check if we're viewing a specific player
    player_name = st.query_params.get("player", None)
    
    if player_name:
        show_player_detail(player_name)
    else:
        show_main_leaderboard()

def show_player_detail(player_name):
    """Show detailed round-by-round breakdown for a specific player."""
    st.title(f"🏌️ {player_name}'s Performance")
    
    # Back button
    if st.button("← Back to Leaderboard"):
        st.query_params.clear()
        st.rerun()
    
    st.markdown("---")
    
    # Load player's round data
    round_data = load_player_round_data(player_name)
    
    if not round_data:
        st.warning("No round data found for this player.")
        return
    
    # Display round-by-round breakdown
    for round_name, data in round_data.items():
        with st.container():
            st.markdown(f"""
            <div style="
                background: linear-gradient(90deg, #2c3e50 0%, #34495e 100%);
                border-radius: 10px;
                padding: 20px;
                margin: 15px 0;
                border-left: 5px solid #3498db;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            ">
                <h3 style="color: #ecf0f1; margin: 0 0 10px 0;">{round_name}</h3>
                <div style="display: flex; justify-content: center; align-items: center;">
                    <div>
                        <span style="color: #bdc3c7; font-size: 0.9em;">Points: </span>
                        <span style="font-size: 2em; font-weight: bold; color: #f39c12;">{data['points']}</span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    
    # Footer with last updated time
    st.markdown("---")
    st.markdown(f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

def show_main_leaderboard():
    """Show the main leaderboard."""
    # Display custom logo and title
    col1, col2 = st.columns([1, 8])
    with col1:
        st.image("jdcvo.png", width=150)
    with col2:
        st.markdown("<h1 style='margin-top: 0; padding-top: 0;'>The 'Jimmy D' Carroll Valley Open</h1>", unsafe_allow_html=True)
    st.markdown("---")
    
    # Load data from Google Sheets and past champions
    df_individual, df_team = load_data_from_sheets()
    past_champions = load_past_champions()
    
    if df_individual is None or df_team is None:
        st.error("Could not load leaderboard data")
        return
    
    # Create two columns
    col1, col2 = st.columns(2)
    
    with col1:
        st.header("Individual Leaderboard")
        
        # Create simplified individual display
        for i, row in df_individual.iterrows():
            rank = row['Rank']
            name = row['Player']
            team = row['Team']
            total = row['Total']
            
            # Get team color
            team_colors = {
                'Red': '#e74c3c',
                'Blue': '#3498db', 
                'Green': '#2ecc71',
                'Yellow': '#f1c40f',
                'Purple': '#9b59b6',
                'Orange': '#e67e22',
                'Pink': '#e91e63',
                'Teal': '#1abc9c',
                'Brown': '#8b4513',
                'Gray': '#95a5a6',
                'Black': '#2d2d2d',
                'White': '#ecf0f1'
            }
            team_color = team_colors.get(team, '#3498db')  # Default to blue if team not found
            
            # Check if player is a past champion or defending champion
            if name == "Gunter":  # Defending champion
                champion_icon = "👑"
            elif name in past_champions:
                champion_icon = "🏆"
            else:
                champion_icon = ""
            
            # Create a styled container for each player
            st.markdown(f"""
            <div style="
                background: linear-gradient(90deg, #2c3e50 0%, #34495e 100%);
                border-radius: 10px;
                padding: 15px;
                margin: 10px 0;
                border-left: 5px solid {team_color};
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            ">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <span style="font-size: 1.2em; font-weight: bold; color: #ecf0f1;">#{rank} <a href="?player={name}" style="color: #ecf0f1; text-decoration: none; cursor: pointer;">{name}</a> <span style="font-size: 0.8em;">{champion_icon}</span></span><br>
                        <span style="color: #bdc3c7; font-size: 0.9em;">{team}</span>
                    </div>
                    <div style="text-align: right;">
                        <span style="font-size: 1.5em; font-weight: bold; color: #f39c12;">{total}</span><br>
                        <span style="color: #bdc3c7; font-size: 0.8em;">points</span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    
    with col2:
        st.header("Team Leaderboard")
        
        # Create simplified team display
        for i, row in df_team.iterrows():
            rank = row['Rank']
            team = row['Team']
            total = row['Total']
            players = row['Players']
            
            # Get team color
            team_colors = {
                'Red': '#e74c3c',
                'Blue': '#3498db', 
                'Green': '#2ecc71',
                'Yellow': '#f1c40f',
                'Purple': '#9b59b6',
                'Orange': '#e67e22',
                'Pink': '#e91e63',
                'Teal': '#1abc9c',
                'Brown': '#8b4513',
                'Gray': '#95a5a6',
                'Black': '#2d2d2d',
                'White': '#ecf0f1'
            }
            team_color = team_colors.get(team, '#e74c3c')  # Default to red if team not found
            
            # Create a styled container for each team
            st.markdown(f"""
            <div style="
                background: linear-gradient(90deg, #2c3e50 0%, #34495e 100%);
                border-radius: 10px;
                padding: 15px;
                margin: 10px 0;
                border-left: 5px solid {team_color};
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            ">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <span style="font-size: 1.2em; font-weight: bold; color: #ecf0f1;">#{rank} {team}</span><br>
                        <span style="color: #bdc3c7; font-size: 0.8em;">{players}</span>
                    </div>
                    <div style="text-align: right;">
                        <span style="font-size: 1.5em; font-weight: bold; color: #f39c12;">{total}</span><br>
                        <span style="color: #bdc3c7; font-size: 0.8em;">points</span>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
    
    # Footer with last updated time
    st.markdown("---")
    st.markdown(f"*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    
    # Auto-refresh every 30 seconds
    time.sleep(30)
    st.rerun()

if __name__ == "__main__":
    main() 