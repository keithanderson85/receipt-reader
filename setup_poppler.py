#!/usr/bin/env python3
"""
Setup script to install poppler for Windows users.
This enables PDF processing functionality in the receipt reader.
"""

import os
import sys
import urllib.request
import zipfile
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def setup_poppler():
    """Download and setup poppler for Windows."""
    
    # Check if we're on Windows
    if sys.platform != 'win32':
        logger.info("This script is for Windows only. On other platforms, install poppler using your package manager.")
        return True
    
    # Check if poppler is already installed
    try:
        import subprocess
        result = subprocess.run(['pdftoppm', '-h'], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("✅ Poppler is already installed and working!")
            return True
    except FileNotFoundError:
        logger.info("Poppler not found, downloading...")
    
    # Define paths
    base_dir = Path.cwd()
    poppler_dir = base_dir / 'poppler'
    zip_file = base_dir / 'poppler-windows.zip'
    
    try:
        # Download poppler
        logger.info("Downloading poppler binaries...")
        url = "https://github.com/oschwartz10612/poppler-windows/releases/download/v24.08.0-0/Release-24.08.0-0.zip"
        urllib.request.urlretrieve(url, zip_file)
        logger.info("✅ Download complete")
        
        # Extract
        logger.info("Extracting poppler...")
        with zipfile.ZipFile(zip_file, 'r') as zip_ref:
            zip_ref.extractall(poppler_dir)
        logger.info("✅ Extraction complete")
        
        # Find the bin directory
        bin_dir = poppler_dir / 'poppler-24.08.0' / 'Library' / 'bin'
        if not bin_dir.exists():
            raise FileNotFoundError(f"Poppler bin directory not found at {bin_dir}")
        
        # Clean up zip file
        zip_file.unlink()
        logger.info("✅ Cleanup complete")
        
        # Test installation
        logger.info("Testing poppler installation...")
        import subprocess
        
        # Add to PATH for this process
        env = os.environ.copy()
        env['PATH'] = str(bin_dir) + os.pathsep + env['PATH']
        
        result = subprocess.run(['pdftoppm', '-h'], env=env, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("✅ Poppler installation successful!")
            
            # Create a batch file to set PATH permanently
            batch_content = f"""@echo off
set PATH=%PATH%;{bin_dir}
echo Poppler PATH added for this session
"""
            with open('set_poppler_path.bat', 'w') as f:
                f.write(batch_content)
            
            logger.info("✅ Created 'set_poppler_path.bat' to set PATH in new sessions")
            logger.info("Run 'set_poppler_path.bat' in new command prompts to enable PDF processing")
            
            return True
        else:
            logger.error("❌ Poppler installation test failed")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error setting up poppler: {e}")
        return False

if __name__ == "__main__":
    logger.info("Starting poppler setup...")
    success = setup_poppler()
    
    if success:
        logger.info("✅ Poppler setup completed successfully!")
        logger.info("PDF processing is now available in the receipt reader.")
    else:
        logger.error("❌ Poppler setup failed!")
        logger.error("PDF processing will not work until poppler is installed.")
    
    exit(0 if success else 1) 