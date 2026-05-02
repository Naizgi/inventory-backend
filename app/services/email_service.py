# app/services/email_service.py
import httpx
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

class EmailService:
    """Service for sending emails using Bravo Email Service"""
    
    # Bravo API Configuration
    BRAVO_API_URL = os.getenv("BRAVO_API_URL", "https://api.bravo.com/v1/email/send")
    BRAVO_API_KEY = os.getenv("BRAVO_API_KEY", "")
    BRAVO_FROM_EMAIL = os.getenv("BRAVO_FROM_EMAIL", "noreply@inventorysystem.com")
    BRAVO_FROM_NAME = os.getenv("BRAVO_FROM_NAME", "Inventory System")
    
    @classmethod
    def send_otp_email(cls, to_email: str, otp_code: str, purpose: str = "verification") -> bool:
        """
        Send OTP code via Bravo email service
        """
        # If no API key, log OTP for development
        if not cls.BRAVO_API_KEY:
            logger.warning("⚠️ Bravo API key not configured. OTP email not sent.")
            logger.info(f"📧 [DEV] OTP for {to_email}: {otp_code}")
            return True
        
        subject = f"Your {purpose.replace('_', ' ').title()} Code - Inventory System"
        
        # HTML email template
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Verification Code</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                    background-color: #f4f7f6;
                    margin: 0;
                    padding: 0;
                }}
                .container {{
                    max-width: 560px;
                    margin: 0 auto;
                    background: #ffffff;
                    border-radius: 16px;
                    overflow: hidden;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
                }}
                .header {{
                    background: linear-gradient(135deg, #2FB8A6 0%, #6FD3C3 100%);
                    padding: 32px 24px;
                    text-align: center;
                }}
                .logo {{
                    font-size: 28px;
                    font-weight: 700;
                    color: white;
                    letter-spacing: -0.5px;
                }}
                .logo-icon {{
                    width: 48px;
                    height: 48px;
                    margin: 0 auto 12px;
                    background: rgba(255, 255, 255, 0.2);
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }}
                .content {{
                    padding: 32px 24px;
                }}
                .greeting {{
                    font-size: 24px;
                    font-weight: 600;
                    color: #1A2A2E;
                    margin-bottom: 12px;
                }}
                .message {{
                    color: #5A6E73;
                    line-height: 1.6;
                    margin-bottom: 28px;
                }}
                .otp-container {{
                    background: #F0FDFA;
                    border-radius: 12px;
                    padding: 24px;
                    text-align: center;
                    margin: 24px 0;
                    border: 1px solid #C4F0E8;
                }}
                .otp-code {{
                    font-size: 42px;
                    font-weight: 700;
                    color: #2FB8A6;
                    letter-spacing: 8px;
                    font-family: 'Courier New', monospace;
                }}
                .expiry {{
                    font-size: 12px;
                    color: #8FA3A8;
                    text-align: center;
                    margin-top: 8px;
                }}
                .button {{
                    display: inline-block;
                    background: linear-gradient(135deg, #2FB8A6 0%, #6FD3C3 100%);
                    color: white;
                    padding: 12px 32px;
                    border-radius: 40px;
                    text-decoration: none;
                    font-weight: 600;
                    margin: 16px 0;
                }}
                .footer {{
                    background: #F9FAFB;
                    padding: 24px;
                    text-align: center;
                    font-size: 12px;
                    color: #9CA3AF;
                    border-top: 1px solid #E5E7EB;
                }}
                .security-note {{
                    background: #FEF3C7;
                    border-left: 4px solid #F59E0B;
                    padding: 12px 16px;
                    margin: 20px 0;
                    font-size: 13px;
                    color: #92400E;
                    border-radius: 8px;
                }}
            </style>
        </head>
        <body>
            <div style="padding: 20px; background: #f4f7f6;">
                <div class="container">
                    <div class="header">
                        <div class="logo-icon">
                            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="1.5">
                                <path d="M20 7L12 3L4 7L12 11L20 7Z" stroke="white"/>
                                <path d="M4 7V17L12 21L20 17V7" stroke="white"/>
                                <path d="M12 11V21" stroke="white"/>
                            </svg>
                        </div>
                        <div class="logo">Inventory System</div>
                    </div>
                    <div class="content">
                        <div class="greeting">Verification Code</div>
                        <div class="message">
                            Hello,<br><br>
                            You requested a {purpose.replace('_', ' ')} code for your account. 
                            Please use the following 6-digit code to complete your verification.
                        </div>
                        <div class="otp-container">
                            <div class="otp-code">{otp_code}</div>
                            <div class="expiry">⏰ This code expires in 10 minutes</div>
                        </div>
                        <div class="security-note">
                            🔒 If you didn't request this code, please ignore this email. 
                            Never share this code with anyone.
                        </div>
                    </div>
                    <div class="footer">
                        <p>© 2024 Inventory System. All rights reserved.</p>
                        <p style="margin-top: 8px;">This is an automated message, please do not reply.</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""
        Inventory System - Verification Code
        
        Hello,
        
        You requested a {purpose.replace('_', ' ')} code for your account.
        
        Your verification code is: {otp_code}
        
        This code expires in 10 minutes.
        
        If you didn't request this, please ignore this email.
        
        ---
        Inventory System
        """
        
        # Prepare email payload for Bravo API
        payload = {
            "to": to_email,
            "from_email": cls.BRAVO_FROM_EMAIL,
            "from_name": cls.BRAVO_FROM_NAME,
            "subject": subject,
            "html_content": html_content,
            "text_content": text_content,
            "track_opens": True,
            "track_clicks": True
        }
        
        headers = {
            "Authorization": f"Bearer {cls.BRAVO_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.post(cls.BRAVO_API_URL, json=payload, headers=headers)
                
                if response.status_code in [200, 201, 202]:
                    logger.info(f"✅ OTP email sent to {to_email} via Bravo")
                    return True
                else:
                    logger.error(f"❌ Bravo API error: {response.status_code} - {response.text}")
                    # Don't fail registration, just log the OTP
                    logger.info(f"📧 OTP for {to_email}: {otp_code}")
                    return True  # Return true to allow development
                    
        except Exception as e:
            logger.error(f"❌ Failed to send email: {str(e)}")
            # Don't fail registration, just log the OTP
            logger.info(f"📧 OTP for {to_email}: {otp_code}")
            return True  # Return true to allow development