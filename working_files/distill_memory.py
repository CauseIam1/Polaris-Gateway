#!/usr/bin/env python3
"""
distill_memory.py - Nightly Fact Extraction for FreeRoam Memory Loop

This script reads user transcripts, asks Ollama to extract new facts,
and appends them to the user's profile.json automatically.

Run via cron or gateway API: python3 distill_memory.py [Rich|Matt]
"""

import requests
import json
import os
import sys
from datetime import datetime

# ============== USER CONFIGURATION ==============
# Map user names to their file paths
USER_FILES = {
    "Rich": {
        "profile_file": "rich_profile.json",
        "transcript_file": "rich_transcripts.log",
        "marker_file": ".rich_processed_line_count"
    },
    "Matt": {
        "profile_file": "matt_profile.json",
        "transcript_file": "matt_transcripts.log",
        "marker_file": ".matt_processed_line_count"
    }
}

OLLAMA_URL = "http://ollama:11434/api/generate"

def get_user_files(user_name):
    """Get file paths for a specific user."""
    if user_name in USER_FILES:
        return USER_FILES[user_name]
    # Default to Rich for backwards compatibility
    return USER_FILES["Rich"]

def load_profile(profile_file):
    """Load existing profile or create new one"""
    if os.path.exists(profile_file):
        with open(profile_file, 'r') as f:
            return json.load(f)
    return {"user_name": "Unknown", "facts_and_preferences": [], "created_at": datetime.now().isoformat()}

def save_profile(profile_file, profile):
    """Save profile to disk"""
    with open(profile_file, 'w') as f:
        json.dump(profile, f, indent=2)

def get_new_transcripts(transcript_file, marker_file):
    """
    Read transcripts that haven't been processed yet.
    Uses a marker file to track how many lines have been processed.
    """
    if not os.path.exists(transcript_file):
        return []
    
    # Get count of previously processed lines
    processed_count = 0
    if os.path.exists(marker_file):
        with open(marker_file, 'r') as f:
            processed_count = int(f.read().strip())
    
    # Read all transcript
    with open(transcript_file, 'r') as f:
        all_lines = f.readlines()
    
    # Get only new lines
    new_lines = all_lines[processed_count:] if processed_count < len(all_lines) else []
    
    # Update marker
    with open(marker_file, 'w') as f:
        f.write(str(len(all_lines)))
    
    return [line.strip() for line in new_lines if line.strip()]

def extract_facts_with_ollama(transcripts):
    """
    Use Ollama to extract facts and preferences from transcript lines.
    """
    if not transcripts:
        return []
    
    transcript_text = "\n".join(transcripts)
    
    prompt = f"""
Analyze the following user statements and extract factual information about:
- User preferences (likes, dislikes, opinions)
- Hardware/setup details (devices, servers, configurations)
- Personal rules or guidelines
- Habits or routines
- Project specifications or constraints

Format each fact as a single concise sentence starting with "User..." or similar.
Do not extract temporary states (mood, immediate actions) - only enduring facts.

USER STATEMENTS:
{transcript_text}

Extract facts (one per line, no bullets):
"""
    
    payload = {
        "model": "my-stark-ai",
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,  # Lower temp for more consistent extraction
            "num_ctx": 4096
        }
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        result = response.json()
        extracted_text = result.get("response", "")
        
        # Parse extracted facts (one per line)
        facts = [
            line.strip() 
            for line in extracted_text.split('\n') 
            if line.strip() and not line.strip().startswith('USER STATEMENTS')
        ]
        
        return facts
    
    except requests.exceptions.RequestException as e:
        print(f"Error extracting facts: {e}", file=sys.stderr)
        return []

def main():
    # Get user name from command line argument
    user_name = sys.argv[1] if len(sys.argv) > 1 else "Rich"
    
    # Get user-specific file paths
    files = get_user_files(user_name)
    profile_file = files["profile_file"]
    transcript_file = files["transcript_file"]
    marker_file = files["marker_file"]
    
    print("=" * 50)
    print(f"🧠 FreeRoam Memory Distillation - {user_name}")
    print("=" * 50)
    
    # Get new transcripts
    new_transcripts = get_new_transcripts(transcript_file, marker_file)
    
    if not new_transcripts:
        print("✓ No new transcripts to process.")
        return
    
    print(f"📝 Found {len(new_transcripts)} new transcript(s)")
    
    # Extract facts
    print("🔍 Extracting facts...")
    new_facts = extract_facts_with_ollama(new_transcripts)
    
    if not new_facts:
        print("✓ No new facts extracted.")
        return
    
    # Load profile and add new facts (avoiding duplicates)
    profile = load_profile(profile_file)
    existing_facts = set(profile.get("facts_and_preferences", []))
    
    added_count = 0
    for fact in new_facts:
        # Check for semantic similarity (simple dedup)
        is_duplicate = any(
            fact.lower() in existing.lower() or existing.lower() in fact.lower()
            for existing in existing_facts
        )
        
        if not is_duplicate and fact not in existing_facts:
            profile["facts_and_preferences"].append(fact)
            existing_facts.add(fact)
            added_count += 1
            print(f"  + Added: {fact}")
    
    if added_count > 0:
        save_profile(profile_file, profile)
        print(f"\n✓ Profile updated: {added_count} new fact(s) added")
        print(f"📊 Total facts in profile: {len(profile['facts_and_preferences'])}")
    else:
        print("\n✓ No new unique facts to add.")
    
    print("=" * 50)

if __name__ == '__main__':
    main()