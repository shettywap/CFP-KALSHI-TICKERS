import os
import time
import json
import base64
from urllib.parse import urlparse, urljoin
from datetime import datetime

import requests
import streamlit as st
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


# ===========================
#  CONFIG / ENV VARS
# ===========================

# Set these in Streamlit Cloud "Secrets":
#
#   KALSHI_API_KEY_ID       = your Kalshi API key ID
#   KALSHI_PRIVATE_KEY_PEM  = your *multi-line* RSA private key in PEM format
#

API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
PRIVATE_KEY_PEM = os.getenv("KALSHI_PRIVATE_KEY_PEM")

# *** CORRECT TRADING API BASE URL ***
BASE_URL = os.getenv(
    "KALSHI_BASE_URL",
    "https://api.kal
