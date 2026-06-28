# API Documentation Descriptions (Markdown)
# Centralized to keep routers clean and readable.

API_DESCRIPTION = """
# 🌌 Heaven's Eye API Gateway

Welcome to the backend API services for **Heaven's Eye**, a state-of-the-art agricultural monitoring and risk assessment platform designed for the Hackathon 2026.

This service provides:
* **Secure Authentication**: JWT-based sign-up, sign-in, and profile operations.
* **Spatial & Plot Management**: Precision polygon drawing, automatic area calculation using PostGIS transformations (EPSG:32647), and user plot boundaries management.
* **Smart Notifications**: Dispatch system notifying farmers via **LINE Bot Messaging API** and **Firebase Cloud Messaging (FCM)**.
* **Real-time Risk Engine**: Dynamic environmental alert scoring (mocked in current MVP iteration).
"""

# Authentication Route Descriptions
AUTH_REGISTER_DESC = (
    "Create a new farmer or admin account in the database. "
    "Generates a secure, salted password hash before storage."
)

AUTH_LOGIN_DESC = (
    "Verify user credentials using OAuth2 password flow and issue a secure bearer token valid for 7 days."
)

AUTH_ME_DESC = (
    "Fetch the profile details of the currently authenticated user using their bearer JWT token."
)

# Plot Management Route Descriptions
PLOT_CREATE_DESC = """
Create a new farm plot boundary record. 

This endpoint receives a GeoJSON polygon in standard WGS84 GPS coordinates (EPSG:4326), projects it into UTM Zone 47N (EPSG:32647) using PostGIS spatial functions (`ST_Transform` & `ST_GeomFromGeoJSON`) to accurately calculate the boundary area in square meters (`ST_Area`), and persists the polygon along with its calculated area.
"""

PLOT_GET_DESC = (
    "Retrieve all farmland plot polygons registered under the authenticated user's account. "
    "Polygons are re-projected to standard WGS84 GPS (EPSG:4326) GeoJSON format for frontend map rendering."
)

PLOT_DELETE_DESC = (
    "Permanently delete a registered farmland plot boundary and its related risk scores using its ID."
)

# Notifications Route Descriptions
NOTIFICATION_SEND_DESC = """
Broadcast a critical notification or crop warning alert to a targeted list of users.

**Note (MVP Architecture)**: 
This is an asynchronous dispatch gateway. In the final production environment, this queues events to be distributed via:
1. **LINE Messaging API** using the `@line/bot-sdk` to reach farmers directly via LINE Chat Bot.
2. **Firebase Cloud Messaging (FCM)** using `firebase-admin` to push real-time alerts to the Heaven's Eye native mobile apps.
"""
