CUSTOM_CSS = """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    
    /* Main Background */
    .stApp {
        background-color: #212121;
        color: #ECECEC;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #171717;
        border-right: 1px solid #2F2F2F;
    }
    [data-testid="stSidebar"] h1, 
    [data-testid="stSidebar"] h2, 
    [data-testid="stSidebar"] h3 {
        color: #ECECEC;
    }
    
    /* Input Fields */
    .stTextInput > div > div > input {
        background-color: #2F2F2F;
        color: #ECECEC;
        border: 1px solid #4A4A4A;
        border-radius: 8px;
        padding: 10px;
        font-family: 'Inter', sans-serif;
    }
    
    /* Chat Input */
    .stChatInput {
        background-color: #2F2F2F;
        border: 1px solid #4A4A4A;
        border-radius: 12px;
        padding: 4px;
    }
    .stChatInput textarea {
        background-color: transparent !important;
        color: #ECECEC !important;
        border: none !important;
        font-family: 'Inter', sans-serif;
    }
    
    /* Buttons */
    .stButton > button {
        /* Subtle vertical gradient for depth */
        background: linear-gradient(180deg, #3A3A3A 0%, #2C2C2C 100%);
        color: #ECECEC;
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-top: 1px solid rgba(255, 255, 255, 0.15); /* Highlight top edge */
        border-radius: 6px; /* Slightly tighter radius */
        font-weight: 500;
        padding: 8px 16px;
        font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
        cursor: pointer;
        position: relative;
        overflow: hidden;
        
        /* Smooth transition */
        transition: all 0.2s cubic-bezier(0.25, 1, 0.5, 1);
        
        /* Subtle shadow */
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
        
        /* Prevent text selection */
        user-select: none;
        -webkit-user-select: none;
    }
    
    /* Primary Action Buttons (Green) override */
    /* We target specific buttons if possible, or use a class. 
       Streamlit buttons are generic, but we can style the default primary look if needed.
       For now, we'll keep the dark neutral base for general buttons and 
       add a specific rule for the "New Chat" or primary actions if we can identify them,
       or just make ALL buttons this premium dark style, 
       and maybe use the gradient ONLY for the "primary" ones if Streamlit exposes that class.
       Streamlit adds .st-emotion-cache-... but it's unstable.
       Let's stick to a refined green for the main buttons as per user preference for "premium".
    */
    .stButton > button {
        background: linear-gradient(180deg, #1F2937 0%, #111827 100%);
        color: #F3F4F6;
        border: 1px solid #374151;
        box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
    }

    /* Hover state */
    .stButton > button:hover {
        background: linear-gradient(180deg, #374151 0%, #1F2937 100%);
        border-color: #4B5563;
        transform: translateY(0);
        box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
    }
    
    /* Active state */
    .stButton > button:active {
        background: #111827;
        box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.2);
        transform: translateY(0.5px);
    }

    /* Focus state */
    .stButton > button:focus-visible {
        outline: 2px solid #3B82F6;
        outline-offset: 2px;
    }
    
    /* Reduced motion */
    @media (prefers-reduced-motion: reduce) {
        .stButton > button {
            transition: none;
        }
    }
    
    /* Headers */
    h1, h2, h3 {
        font-family: 'Inter', sans-serif;
        color: #ECECEC;
        font-weight: 600;
        letter-spacing: -0.02em;
    }
    
    /* Chat Messages */
    .stChatMessage {
        background-color: transparent !important;
        padding: 1.5rem 0;
    }
    [data-testid="stChatMessageContent"] {
        background-color: #2F2F2F;
        border-radius: 16px;
        padding: 1.25rem 1.5rem;
        border: 1px solid #3F3F3F;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
    }
    
    /* User messages - slightly different color */
    .stChatMessage[data-testid*="user"] [data-testid="stChatMessageContent"] {
        background-color: #343541;
        border: 1px solid #444654;
    }
    
    /* Status/Expander */
    [data-testid="stStatus"] {
        background: rgba(47, 47, 47, 0.6);
        backdrop-filter: blur(10px);
        border: 1px solid #4A4A4A;
        border-radius: 12px;
        padding: 0.75rem;
    }
    
    /* Dataframe styling */
    .stDataFrame {
        border-radius: 8px;
        overflow: hidden;
    }
    
    /* Tool call badges */
    .tool-badge {
        display: inline-block;
        background: #2C2C2C; /* Dark neutral background */
        border: 1px solid #3F3F3F;
        color: #E5E7EB;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 500;
        margin: 4px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.1);
    }
    
    /* Secondary button styling */
    button[kind="secondary"] {
        background: transparent !important;
        color: #9CA3AF !important; /* Muted gray */
        border: 1px solid transparent !important;
        border-radius: 6px !important;
        font-weight: 500 !important;
        padding: 8px 16px !important;
        
        transition: all 0.2s cubic-bezier(0.25, 1, 0.5, 1) !important;
        box-shadow: none !important;
    }
    
    button[kind="secondary"]:hover {
        background: rgba(239, 68, 68, 0.1) !important; /* Subtle red tint */
        color: #EF4444 !important; /* Red text */
        border-color: rgba(239, 68, 68, 0.2) !important;
        transform: none !important;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05) !important;
    }
    
    button[kind="secondary"]:active {
        background: rgba(239, 68, 68, 0.15) !important;
        transform: scale(0.98) !important;
    }
    
    button[kind="secondary"]:focus-visible {
        outline: 2px solid #EF4444 !important;
        outline-offset: 2px !important;
    }
    
    /* Selectbox styling */
    .stSelectbox > div > div {
        background-color: #2F2F2F !important;
        border: 1px solid #4A4A4A !important;
        border-radius: 8px !important;
    }
    .stSelectbox > div > div:hover {
        border-color: #10A37F !important;
    }
    
    /* Toast notifications */
    .stToast {
        background: #2F2F2F !important;
        border: 1px solid #10A37F !important;
        border-radius: 8px !important;
    }
    
    /* Animation for thinking */
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
    }
    .thinking {
        animation: pulse 1.5s ease-in-out infinite;
    }
    
    /* Smooth transitions for interactive elements - performant properties only */
    .stSelectbox > div > div, 
    .stTextInput input {
        transition: border-color 0.2s ease-out,
                    background-color 0.2s ease-out,
                    box-shadow 0.2s ease-out;
    }
    
    /* Reduced motion override for all transitions */
    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after {
            animation-duration: 0.01ms !important;
            animation-iteration-count: 1 !important;
            transition-duration: 0.01ms !important;
        }
        
        .thinking {
            animation: none !important;
            opacity: 1 !important;
        }
    }
    </style>
"""
