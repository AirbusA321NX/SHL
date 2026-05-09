const chatWindow = document.getElementById('chat-window');
const userInput = document.getElementById('user-input');
const btnSend = document.getElementById('btn-send');
const progressBar = document.getElementById('progress-bar-fill');
const turnCountLabel = document.getElementById('turn-count');
const recPanel = document.getElementById('recommendation-panel');
const recList = document.getElementById('rec-list');
const chatListContainer = document.getElementById('chat-list');
const btnNewChat = document.getElementById('btn-new-chat');

// Configure Marked.js and Mermaid.js for Diagrams
function unescapeHtml(text) {
    return text
        .replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'");
}

const renderer = new marked.Renderer();
const originalCode = renderer.code.bind(renderer);
renderer.code = function({text, lang, escaped}) {
  if (lang === 'mermaid') {
    return `<div class="mermaid">${unescapeHtml(text)}</div>`;
  }
  // fallback for older marked versions
  if (arguments.length > 1 && typeof arguments[0] === 'string') {
      const [codeStr, language] = arguments;
      if (language === 'mermaid') return `<div class="mermaid">${unescapeHtml(codeStr)}</div>`;
      return originalCode(codeStr, language, arguments[2]);
  }
  return originalCode({text, lang, escaped});
};
marked.setOptions({ renderer });
mermaid.initialize({ startOnLoad: false, theme: 'dark' });

const MAX_TURNS = 8;
let chats = JSON.parse(localStorage.getItem('shl_chats')) || [];
let currentChatId = null;

// Initialize
if (chats.length === 0) {
    createNewChat();
} else {
    loadChat(chats[0].id);
}

// Event Listeners
btnSend.addEventListener('click', sendMessage);
btnNewChat.addEventListener('click', createNewChat);
userInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

function createNewChat() {
    const id = Date.now().toString();
    const newChat = {
        id,
        title: "New Chat",
        messages: [],
        turnCount: 0,
        recommendations: []
    };
    chats.unshift(newChat);
    saveChats();
    loadChat(id);
}

function saveChats() {
    localStorage.setItem('shl_chats', JSON.stringify(chats));
    renderSidebar();
}

function renderSidebar() {
    if (!chatListContainer) return;
    chatListContainer.innerHTML = '';
    chats.forEach(chat => {
        const li = document.createElement('li');
        li.className = `chat-item ${chat.id === currentChatId ? 'active' : ''}`;
        li.innerHTML = `
            <div class="chat-item-title" onclick="loadChat('${chat.id}')" style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer;">
                <i class="far fa-comment-alt"></i> ${chat.title}
            </div>
            <button class="btn-delete-chat" onclick="deleteChat('${chat.id}', event)" title="Delete Chat">
                <i class="fas fa-trash-alt"></i>
            </button>
        `;
        chatListContainer.appendChild(li);
    });
}

function deleteChat(id, event) {
    if (event) event.stopPropagation();
    chats = chats.filter(c => c.id !== id);
    if (chats.length === 0) {
        createNewChat();
    } else if (currentChatId === id) {
        loadChat(chats[0].id);
    }
    saveChats();
}

function loadChat(id) {
    currentChatId = id;
    const chat = chats.find(c => c.id === id);
    if (!chat) return;
    
    // Clear UI
    chatWindow.innerHTML = `
        <div class="message system">
            <div class="avatar">AGENT</div>
            <div class="text">Hello! I'm your SHL Assessment Recommender. How can I help you find the right talent solutions today?</div>
        </div>
    `;
    
    // Restore history
    if (chat.messages) {
        chat.messages.forEach(m => {
            appendMessage(m.role, m.content, m.role === 'user');
        });
    }
    
    updateTurnLimit();
    updateRecommendations(chat.recommendations || []);
    renderSidebar();
}

function getCurrentChat() {
    return chats.find(c => c.id === currentChatId);
}

async function sendMessage() {
    const text = userInput.value.trim();
    const currentChat = getCurrentChat();
    
    if (!text || currentChat.turnCount >= MAX_TURNS) return;

    // Add user message
    appendMessage('user', text, true);
    currentChat.messages.push({ role: 'user', content: text });
    userInput.value = '';
    
    currentChat.turnCount++;
    saveChats();
    updateTurnLimit();

    // Trigger Title Generation for new chats asynchronously
    if (currentChat.messages.length === 1 && currentChat.title === "New Chat") {
        fetch('/generate_title', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: text })
        }).then(res => res.json()).then(data => {
            if (data.title) {
                currentChat.title = data.title;
                saveChats();
            }
        }).catch(err => console.error("Title gen failed", err));
    }

    const typingId = appendMessage('system', '<div class="typing-indicator"><span></span><span></span><span></span></div>', true);

    try {
        let response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ messages: currentChat.messages })
        });
        
        let data = await response.json();
        let finalReply = data.reply;
        let finalRecs = data.recommendations || [];
        
        // --- AI SELF-CORRECTION LOOP (MERMAID SYNTAX CHECK) ---
        const mermaidRegex = /```mermaid\s+([\s\S]*?)```/g;
        let match;
        let hasError = false;
        let errorMessage = "";
        
        // We reset lastIndex just in case
        mermaidRegex.lastIndex = 0;
        while ((match = mermaidRegex.exec(finalReply)) !== null) {
            const code = unescapeHtml(match[1].trim());
            try {
                // In Mermaid v9, parse throws if syntax is invalid
                mermaid.parse(code);
            } catch (e) {
                hasError = true;
                errorMessage = e.message || e.str || "Syntax Error";
                break;
            }
        }
        
        if (hasError) {
            console.warn("Mermaid Syntax Error Detected! Initiating silent AI self-correction...", errorMessage);
            const retryMessages = [
                ...currentChat.messages,
                { role: 'assistant', content: finalReply },
                { role: 'user', content: `Your previous mermaid code had a syntax error: ${errorMessage}. Please output your ENTIRE response again exactly as before, but FIX the mermaid syntax. Ensure you quote any node labels containing special characters or parentheses.` }
            ];
            
            try {
                let retryResponse = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ messages: retryMessages })
                });
                let retryData = await retryResponse.json();
                finalReply = retryData.reply;
                finalRecs = retryData.recommendations || [];
                console.log("Self-correction successful!");
            } catch (retryError) {
                console.error("Self-correction API call failed", retryError);
            }
        }
        // --- END SELF-CORRECTION LOOP ---
        
        removeMessage(typingId);
        appendMessage('system', finalReply);
        
        currentChat.messages.push({ role: 'assistant', content: finalReply });
        currentChat.recommendations = finalRecs;
        saveChats();
        
        updateRecommendations(currentChat.recommendations);
        
    } catch (error) {
        console.error('Error:', error);
        removeMessage(typingId);
        appendMessage('system', 'Sorry, I encountered an error. Please try again.', true);
    }
}

function appendMessage(role, text, isRaw = false) {
    const id = Date.now();
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    msgDiv.id = `msg-${id}`;
    
    const avatar = role === 'system' ? 'AGENT' : 'USER';
    const content = (role === 'system' && !isRaw) ? marked.parse(text || "") : (text || "");
    
    msgDiv.innerHTML = `
        <div class="avatar">${avatar}</div>
        <div class="text">${content}</div>
    `;
    
    chatWindow.appendChild(msgDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
    
    if (role === 'system' && !isRaw) {
        try {
            mermaid.init(undefined, document.querySelectorAll(`#msg-${id} .mermaid`));
        } catch (e) {
            console.error("Mermaid parsing error:", e);
        }
    }
    
    return id;
}

function removeMessage(id) {
    const el = document.getElementById(`msg-${id}`);
    if (el) el.remove();
}

function updateTurnLimit() {
    const currentChat = getCurrentChat();
    if (!currentChat) return;
    const count = currentChat.turnCount;
    
    turnCountLabel.innerText = count;
    const percentage = (count / MAX_TURNS) * 100;
    progressBar.style.width = `${percentage}%`;
    
    if (count >= 7) progressBar.style.background = 'var(--danger-red)';
    else if (count >= 5) progressBar.style.background = 'var(--warning-yellow)';
    else progressBar.style.background = 'linear-gradient(90deg, var(--success-green), var(--accent-cyan))';
}

function updateRecommendations(recs) {
    if (!recs || recs.length === 0) {
        recPanel.classList.add('hidden');
        recList.innerHTML = '';
        return;
    }

    recPanel.classList.remove('hidden');
    recList.innerHTML = '';
    
    recs.forEach(rec => {
        const card = document.createElement('div');
        card.className = 'rec-card';
        
        const strengthClass = rec.match_strength === 'Strong Match' ? 'strong-match' : 'good-match';
        const strengthHtml = `<div class="match-badge ${strengthClass}">${rec.match_strength || 'Good Match'}</div>`;
        
        let keywordsHtml = '';
        if (rec.matched_keywords && rec.matched_keywords.length > 0) {
            keywordsHtml = '<div class="keywords-container">' + 
                rec.matched_keywords.map(kw => `<span class="keyword-tick">${kw} <i class="fas fa-check-circle"></i></span>`).join('') + 
                '</div>';
        }
        
        card.innerHTML = `
            ${strengthHtml}
            <h4>${rec.name}</h4>
            <span class="type">${rec.test_type}</span>
            ${keywordsHtml}
            <br>
            <a href="${rec.url || '#'}" target="_blank">View Product Page <i class="fas fa-external-link-alt"></i></a>
        `;
        recList.appendChild(card);
    });
}
