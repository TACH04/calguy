document.addEventListener('DOMContentLoaded', () => {
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const chatWindow = document.getElementById('chat-window');
    const contextList = document.getElementById('context-list');
    const resetBtn = document.getElementById('reset-btn');
    const toastContainer = document.getElementById('toast-container');
    
    let isWaiting = false;

    // Load initial history
    fetchHistory();

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !isWaiting) sendMessage();
    });

    sendBtn.addEventListener('click', () => {
        if (!isWaiting) sendMessage();
    });

    resetBtn.addEventListener('click', async () => {
        await fetch('/api/reset', { method: 'POST' });
        chatWindow.innerHTML = '<div class="message system-msg"><p>Memory cleared. How can I help you schedule your day?</p></div>';
        contextList.innerHTML = '';
        showToast('Memory Reset', 'info');
    });

    async function fetchHistory() {
        try {
            const res = await fetch('/api/history');
            const history = await res.json();
            contextList.innerHTML = '';
            
            history.forEach(msg => {
                const tokens = msg.tokens || 50;
                if (msg.role === 'system') {
                    addContextItem('System', 'Base prompt loaded.', 'system', tokens);
                } else if (msg.role === 'user') {
                    addContextItem('User', msg.content, 'user', tokens);
                } else if (msg.role === 'assistant') {
                    if (msg.tool_calls) {
                        msg.tool_calls.forEach(tc => {
                            addContextItem(`Tool Call: ${tc.function.name}`, JSON.stringify(tc.function.arguments), 'tool', tokens / msg.tool_calls.length);
                        });
                    }
                    if (msg.content) {
                        addContextItem('Assistant', msg.content.substring(0, 50) + '...', 'assistant', tokens);
                    }
                } else if (msg.role === 'tool') {
                    addContextItem(`Tool Result: ${msg.name}`, 'Executed successfully.', 'system', tokens);
                }
            });
        } catch (e) {
            console.error('Error fetching history:', e);
        }
    }

    async function sendMessage() {
        const text = chatInput.value.trim();
        if (!text) return;

        chatInput.value = '';
        appendMessage(text, 'user-msg');
        // Let the backend sync add context items for new messages to get accurate token counts
        // contextList.innerHTML = ''; fetchHistory(); // we will fetch at the end

        
        isWaiting = true;
        const typingId = showTyping();

        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text })
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");

            let done = false;
            let currentAssistantMsgContainer = null;

            while (!done) {
                const { value, done: readerDone } = await reader.read();
                done = readerDone;
                if (value) {
                    const chunk = decoder.decode(value, { stream: true });
                    const lines = chunk.split('\n');
                    
                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const data = JSON.parse(line.substring(6));
                            
                            // Handle different event types from backend
                            if (data.type === 'status') {
                                // show status in context bar or typing indicator
                                addContextItem('System Status', data.content, 'system', data.tokens || 10);
                            } else if (data.type === 'tool_call') {
                                removeTyping(typingId);
                                
                                appendStep(`Tool Call: ${data.tool}`, JSON.stringify(data.args, null, 2));
                                addContextItem(`Tool Call: ${data.tool}`, JSON.stringify(data.args), 'tool', data.tokens || 50);
                                
                                showTyping(); // re-add typing while tool executes
                            } else if (data.type === 'tool_result') {
                                appendStep(`Tool Result`, data.result);
                                addContextItem(`Tool Result`, data.result.substring(0, 50) + '...', 'system', data.tokens || 50);
                                if (data.tool.includes('create') || data.tool.includes('delete')) {
                                    showToast(`Calendar Action Confirmed: ${data.tool}`, 'success');
                                }
                            } else if (data.type === 'message') {
                                removeTyping();
                                if (!currentAssistantMsgContainer) {
                                    currentAssistantMsgContainer = appendMessage('', 'agent-msg');
                                }
                                const p = document.createElement('p');
                                // Simple markdown formatting
                                p.innerHTML = data.content.replace(/\n/g, '<br>');
                                currentAssistantMsgContainer.appendChild(p);
                                fetchHistory(); // full sync
                                currentAssistantMsgContainer = null;
                            } else if (data.type === 'error') {
                                removeTyping();
                                appendMessage(`Error: ${data.content}`, 'system-msg');
                            }
                        }
                    }
                }
            }
        } catch (e) {
            console.error(e);
            removeTyping();
            appendMessage(`Connection error.`, 'system-msg');
        } finally {
            isWaiting = false;
            removeTyping();
        }
    }

    function appendMessage(text, className) {
        const div = document.createElement('div');
        div.className = `message ${className}`;
        if (text) {
            const p = document.createElement('p');
            p.textContent = text;
            div.appendChild(p);
        }
        chatWindow.appendChild(div);
        chatWindow.scrollTop = chatWindow.scrollHeight;
        return div;
    }

    function showTyping() {
        const div = document.createElement('div');
        div.id = 'typing-indicator';
        div.className = 'typing';
        div.innerHTML = '<span></span><span></span><span></span>';
        chatWindow.appendChild(div);
        chatWindow.scrollTop = chatWindow.scrollHeight;
        return div.id;
    }

    function removeTyping() {
        const typing = document.getElementById('typing-indicator');
        if (typing) typing.remove();
    }

    function appendStep(title, details) {
        const div = document.createElement('div');
        div.className = 'llm-step';
        div.innerHTML = `
            <div class="llm-step-header">
                <div class="llm-step-icon">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="13 2 13 9 20 9"></polyline><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"></path></svg>
                </div>
                <span>${title}</span>
            </div>
            <div class="llm-step-details">${details.replace(/</g, "&lt;")}</div>
        `;
        div.addEventListener('click', () => {
            div.classList.toggle('expanded');
        });
        chatWindow.appendChild(div);
        chatWindow.scrollTop = chatWindow.scrollHeight;
        return div;
    }

    function addContextItem(title, snippet, type, tokens = 100) {
        const MAX_CONTEXT = 8192; // Updated to 8K limit
        const card = document.createElement('div');
        card.className = `context-card ${type}`;
        
        // Ensure at least 0.5% height to be visible
        const heightPct = Math.max((tokens / MAX_CONTEXT) * 100, 0.5); 
        card.style.setProperty('--token-height', `${heightPct}%`);
        
        card.innerHTML = `<strong>${title}</strong>${snippet.replace(/</g, "&lt;")}`;
        card.title = `${title}: ~${tokens} tokens`;
        
        contextList.appendChild(card);
        contextList.scrollTop = 0;
    }

    function showToast(message, type) {
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
            <span>${message}</span>
        `;
        toastContainer.appendChild(toast);
        
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }
});
