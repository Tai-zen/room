from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import threading
import time
import re # Essential for powerful word filtering

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key' 
socketio = SocketIO(app)

# --- Global Configuration & Data Structures ---
# Retention time: 2 hours (7200 seconds)
HISTORY_RETENTION_SECONDS = 2 * 60 * 60 
# Purge check interval: Check every 5 minutes (300 seconds)
PURGE_INTERVAL_SECONDS = 5 * 60 

# Global structures for anonymous chat
# Maps Session ID (sid) to the user's alias
user_aliases = {}
current_anon_id = 0
alias_lock = threading.Lock()

# Global structure for chat history (with thread safety)
# Format: [{'alias': 'name', 'msg': 'text', 'timestamp': 1678886400.0}, ...]
chat_history = []
history_lock = threading.Lock()

# --- CENSORSHIP LOGIC ---
# List of words to censor. Use full words to avoid false positives.
BAD_WORDS = ['hate', 'harass', 'slur', 'offensive', 'naughty','adult', 'ass', 'asshole', 'bastard', 'bitch', 'blowjob', 'boob', 'bullsh', 'cock', 'crap', 'cocksucker', 'cum', 'damn', 'dick', 'douche', 'fag', 'fuck', 'goddamn', 'hardsex', 'hell', 'homo', 'idiot', 'jerk', 'kink', 'kissass', 'licking', 'masturb', 'motherfucker', 'naked', 'orgasm', 'orgy', 'penis', 'pussy', 'queer', 'retarded', 'semen', 'sexy', 'shit', 'slut', 'snatch', 'sonofabitch', 'suck','tits', 'whore', 'wank','chink', 'coon', 'cripple', 'dyke', 'faggot', 'gipsy', 'gook', 'handicapped', 'inbreed', 'kraut', 'kike', 'lesbian', 'mick', 'nazi', 'nazi', 'nigger', 'queer', 'redskin', 'retard', 'schlomo', 'spastic', 'terrorist', 'tranny', 'wetback', 'whitey', 'abuse', 'attack', 'bully', 'cheat', 'die', 'death', 'exterminate', 'fight', 'kill', 'murder', 'rape', 'suicide', 'threat', 'weapon', 'violent', 'victim', 'bully', 'blackmail', 'harass', 'stalk', 'dox', 'poison', 'torture', 'mutilate','cannabis', 'heroin', 'joint', 'kush', 'lsd', 'marijuana', 'meth', 'narcotic', 'opium', 'smoke', 'weed', 'xanax', 'molly','anal', 'bondage', 'bdsm', 'ejaculation', 'erotic', 'fetish', 'incest', 'intercourse', 'lubricate', 'nude', 'porn', 'prostitution', 'rimjob', 'sex', 'sexual', 'threesome', 'vibrator', 'voyeur', 'zoophil',  'a$$h0l3', 'b!tch', 'c0ck', 'd!ck', 'fvck', 'sh!t', '$hit', '$lut', '//hore', 'wh0re', 
'scam', 'fraud', 'criminal', 'pedo', 'grooming', 'exploitation', 'degenerate', 'deplorable', 'worthless', 'trash', 'loser', 'ass'] 

def censor_message(msg):
    """
    Censors bad words in the message using regular expressions 
    to respect word boundaries (e.g., won't censor 'mass' in 'massive').
    """
    censored_msg = msg
    for word in BAD_WORDS:
        # \b ensures word boundaries; re.IGNORECASE makes it case-insensitive
        pattern = r'\b' + re.escape(word) + r'\b'
        
        # Create a replacement string of asterisks matching the word length
        replacement = '*' * len(word)
        
        # Replace all occurrences
        censored_msg = re.sub(pattern, replacement, censored_msg, flags=re.IGNORECASE)
        
    return censored_msg

# --- Background Purge Logic (Restructured for stability) ---
def purge_messages_loop():
    """Runs a continuous loop in a background thread to purge old messages."""
    global chat_history
    
    # We use a while True loop and time.sleep() instead of threading.Timer
    # to avoid the resource issues related to restarting timers.
    while True:
        try:
            print(f"[PURGE] Starting message purge check at {time.strftime('%H:%M:%S')}")
            
            # Calculate the cutoff time (now minus 2 hours)
            cutoff_time = time.time() - HISTORY_RETENTION_SECONDS
            
            with history_lock:
                # Filter out messages that are older than the cutoff time
                new_history = [
                    msg for msg in chat_history if msg['timestamp'] > cutoff_time
                ]
                
                removed_count = len(chat_history) - len(new_history)
                
                if removed_count > 0:
                    chat_history = new_history
                    print(f"[PURGE] Removed {removed_count} old messages. History size: {len(chat_history)}")
                else:
                    print(f"[PURGE] No messages removed. History size: {len(chat_history)}")

        except Exception as e:
            # Catch exceptions within the thread to keep the server running
            print(f"[PURGE ERROR] An exception occurred in the purge loop: {e}")
        
        # Wait for the defined interval before running the check again
        time.sleep(PURGE_INTERVAL_SECONDS)

# --- ROUTES (Serving the UI) ---
@app.route('/')
def index():
    """Renders the main chatroom HTML page."""
    return render_template('index.html')

# --- SOCKET.IO EVENT HANDLERS (Real-time Communication) ---

@socketio.on('connect')
def handle_connect():
    """Assigns an anonymous alias and sends history upon a new client connection."""
    global current_anon_id
    sid = request.sid
    
    with alias_lock:
        current_anon_id += 1
        alias = f"Anon-User-{current_anon_id}"
        user_aliases[sid] = alias
        
    print(f"[NEW CONNECTION] {alias} connected (SID: {sid}).")
    
    # 1. Send Alias back to the user only
    emit('set_alias', {'alias': alias})
    
    # 2. Send entire current history to the newly connected client only
    with history_lock:
        # Send only the data required for display (alias and msg)
        history_to_send = [
            {'alias': msg['alias'], 'msg': msg['msg']} 
            for msg in chat_history
        ]
        emit('history', {'messages': history_to_send})
    
    # 3. Broadcast join message to everyone else
    emit('message', {'alias': 'SERVER', 'msg': f'{alias} has joined the chat.'}, 
         broadcast=True, include_self=False)

@socketio.on('disconnect')
def handle_disconnect():
    """Removes the alias and broadcasts a disconnect message."""
    sid = request.sid
    
    with alias_lock:
        alias = user_aliases.pop(sid, 'Unknown User')
        
    print(f"[DISCONNECTED] {alias} disconnected (SID: {sid}).")
    
    # Broadcast leave message to everyone else
    emit('message', {'alias': 'SERVER', 'msg': f'{alias} has left the chat.'}, 
         broadcast=True, include_self=False)

@socketio.on('send_message')
def handle_send_message(data):
    """Receives, censors, stores, and broadcasts a message to all clients."""
    sid = request.sid
    # Retrieve the alias from the session ID
    alias = user_aliases.get(sid, 'Unknown Anon')
    msg = data.get('msg')
    
    if msg:
        # --- CENSORSHIP APPLIED HERE ---
        censored_msg = censor_message(msg)

        print(f"[{alias}]: Original: '{msg}' | Censored: '{censored_msg}'")
        
        # 1. Store the CENSORED message with a timestamp
        message_data = {
            'alias': alias, 
            'msg': censored_msg, 
            'timestamp': time.time() # Store current Unix timestamp
        }
        with history_lock:
            chat_history.append(message_data)
        
        # 2. Broadcast the CENSORED message immediately
        emit('message', {'alias': alias, 'msg': censored_msg}, broadcast=True)

if __name__ == '__main__':
    # Initialize and start the background thread using the new loop function.
    # daemon=True ensures the thread dies when the main process exits.
    purge_thread = threading.Thread(target=purge_messages_loop, daemon=True)
    purge_thread.start()
    
    # Import request locally as Flask documentation suggests when using it globally
    from flask import request 
    socketio.run(app, host='0.0.0.0', port=5050, debug=True)