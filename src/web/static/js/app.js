document.addEventListener('DOMContentLoaded', () => {
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const chatWindow = document.getElementById('chat-window');
    const contextList = document.getElementById('context-list');
    const resetBtn = document.getElementById('reset-btn');
    const toastContainer = document.getElementById('toast-container');
    const contextModal = document.getElementById('context-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const closeModalBtn = document.getElementById('close-modal');
    const tokenCount = document.getElementById('token-count');
    const stopBtn = document.getElementById('stop-btn');
    let currentSubAssistantMsgContainer = null;
    
    let chatMessagesRendered = 0;
    
    // Switch Elements
    const switchToMainBtn = document.getElementById('switch-to-main');
    const switchToSubBtn = document.getElementById('switch-to-sub');
    const paneMain = document.getElementById('pane-main');
    const paneSub = document.getElementById('pane-sub');
    const subBadge = document.getElementById('sub-badge');
    const subChatWindow = document.getElementById('sub-chat-window');
    const subContextList = document.getElementById('sub-context-list');
    const subTokenCount = document.getElementById('sub-token-count');
    const subStatusText = document.getElementById('sub-status-text');
    
    let appConfig = {
        OLLAMA_NUM_CTX: 8192 // default fallback
    };

    async function fetchConfig() {
        try {
            const res = await fetch('/api/config');
            const config = await res.json();
            appConfig = config;
            console.log("App configuration loaded:", appConfig);
            // Refresh history/display after config is loaded to ensure scaling is correct
            fetchHistory();
        } catch (e) {
            console.error("Failed to fetch app config:", e);
        }
    }
    
    // Tab Switching Logic
    function switchTab(tabId) {
        if (tabId === 'main') {
            paneMain.classList.add('active');
            paneSub.classList.remove('active');
        } else {
            paneMain.classList.remove('active');
            paneSub.classList.add('active');
        }
    }

    if (switchToMainBtn) switchToMainBtn.addEventListener('click', () => switchTab('main'));
    if (switchToSubBtn) switchToSubBtn.addEventListener('click', () => switchTab('sub'));

    let isWaiting = false;
    let abortController = null;

    // Load initial config and history
    fetchConfig();

    function estimateTokens(text) {
        if (!text) return 0;
        return Math.floor(text.length / 4);
    }

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !isWaiting) sendMessage();
    });

    sendBtn.addEventListener('click', () => {
        if (!isWaiting) sendMessage();
    });

    stopBtn.addEventListener('click', () => {
        if (isWaiting && abortController) {
            abortController.abort();
            showToast('Request Stopped', 'info');
        }
    });

    resetBtn.addEventListener('click', async () => {
        await fetch('/api/reset', { method: 'POST' });
        chatWindow.innerHTML = '<div class="message system-msg"><p>Memory cleared. How can I help you schedule your day?</p></div>';
        chatMessagesRendered = 0; // Reset tracking
        fetchHistory(); // sync new history (contains system prompt)
        showToast('Memory Reset', 'info');
    });

    // Modal Close Logic
    closeModalBtn.addEventListener('click', () => {
        contextModal.classList.remove('active');
    });

    contextModal.addEventListener('click', (e) => {
        if (e.target === contextModal) {
            contextModal.classList.remove('active');
        }
    });

    function showContextModal(title, content) {
        modalTitle.textContent = title;
        modalBody.textContent = content;
        contextModal.classList.add('active');
    }

    async function fetchHistory() {
        try {
            const res = await fetch('/api/history');
            const history = await res.json();
            
            const existingCards = contextList.children;

            // --- Chat Window Sync ---
            // If we have non-system history and haven't rendered yet, clear the welcome message
            if (chatMessagesRendered === 0 && history.length > 1) {
                const hasRealContent = history.some(m => m.role !== 'system');
                if (hasRealContent) {
                    chatWindow.innerHTML = '';
                }
            }
            
            history.forEach((msg, index) => {
                let role = 'system';
                let title = 'System';
                let snippet = msg.content || '';
                let fullContent = msg.content || '';
                let tokens = msg.tokens || 50;

                if (msg.role === 'user') {
                    role = 'user';
                    title = 'User';
                } else if (msg.role === 'assistant') {
                    role = 'assistant';
                    title = 'Assistant';
                    if (msg.tool_calls) {
                        // Just use the first tool call name for the title in the bar if multiple
                        const tc = msg.tool_calls[0];
                        title = `Tool Call: ${tc.function.name}`;
                        fullContent = JSON.stringify(tc.function.arguments, null, 2);
                        snippet = fullContent;
                    }
                    if (msg.content) snippet = msg.content.substring(0, 50) + '...';
                } else if (msg.role === 'tool') {
                    role = 'tool';
                    title = `Tool Result: ${msg.name}`;
                    fullContent = msg.content;
                    snippet = 'Executed successfully.';
                }

                // --- Sidebar Update ---
                if (index < existingCards.length) {
                    // Update existing card
                    updateContextItem(existingCards[index], title, snippet, role, tokens, fullContent);
                } else {
                    // Append new card
                    addContextItem(title, snippet, role, tokens, fullContent);
                }

                // --- Chat Window Update ---
                if (index >= chatMessagesRendered) {
                    if (msg.role === 'user') {
                        appendMessage(msg.content, 'user-msg');
                    } else if (msg.role === 'assistant') {
                        if (msg.content) {
                            const p = document.createElement('p');
                            p.innerHTML = msg.content.replace(/\n/g, '<br>');
                            const div = appendMessage('', 'agent-msg');
                            div.appendChild(p);
                        }
                        if (msg.tool_calls) {
                            msg.tool_calls.forEach(tc => {
                                appendStep(`Tool Call: ${tc.function.name}`, JSON.stringify(tc.function.arguments, null, 2));
                            });
                        }
                    } else if (msg.role === 'tool') {
                        appendStep(`Tool Result: ${msg.name}`, msg.content);
                    } else if (msg.role === 'system' && msg.is_memory) {
                        appendMessage(msg.content, 'system-msg');
                    }
                    chatMessagesRendered++;
                }
            });

            // Remove extra cards if history shrunk (e.g. after reset)
            while (contextList.children.length > history.length) {
                contextList.lastChild.remove();
            }
            updateTotalTokenDisplay();
        } catch (e) {
            console.error('Error fetching history:', e);
        }
    }


    async function sendMessage() {
        const text = chatInput.value.trim();
        if (!text) return;

        chatInput.value = '';
        appendMessage(text, 'user-msg');
        chatMessagesRendered++;
        addContextItem('User', text, 'user', estimateTokens(text));
        
        isWaiting = true;
        abortController = new AbortController();
        
        // Update UI
        sendBtn.style.display = 'none';
        stopBtn.style.display = 'flex';
        
        const typingId = showTyping();

        try {
            const response = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text }),
                signal: abortController.signal
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder("utf-8");

            let done = false;
            let currentAssistantMsgContainer = null;
            let lineBuffer = "";

            while (!done) {
                const { value, done: readerDone } = await reader.read();
                done = readerDone;
                if (value) {
                    const chunk = decoder.decode(value, { stream: true });
                    lineBuffer += chunk;
                    
                    const lines = lineBuffer.split('\n');
                    // Keep the last partial line in the buffer
                    lineBuffer = lines.pop();
                    
                    for (const line of lines) {
                        const trimmedLine = line.trim();
                        if (!trimmedLine || !trimmedLine.startsWith('data: ')) continue;
                        
                        try {
                            const data = JSON.parse(trimmedLine.substring(6));
                            
                            // Handle different event types from backend
                            if (data.type === 'status') {
                                if (data.content.toLowerCase().includes('compressing')) {
                                    showToast('Memory Compressing...', 'info');
                                }
                                updateTyping(data.content);
                            } else if (data.type === 'debug_event' || data.type === 'debug_stream') {
                                // Log trace events to console for now, prevents them being ignored or causing issues
                                console.log(`[Trace: ${data.category}]`, data.content);
                                if (data.type === 'debug_stream') {
                                    // if it's subagent briefing, show it as subagent thought
                                    if (data.category === 'briefing') {
                                        showSubTyping('Extracting Context...');
                                    }
                                }
                            } else if (data.type === 'compressed') {
                                await fetchHistory();
                            } else if (data.type === 'tool_call') {
                                updateTyping(null);
                                appendStep(`Tool Call: ${data.tool}`, JSON.stringify(data.args, null, 2));
                                chatMessagesRendered++;
                                addContextItem(`Tool Call: ${data.tool}`, JSON.stringify(data.args), 'assistant', data.tokens || 50, JSON.stringify(data.args, null, 2));
                                showTyping();
                            } else if (data.type === 'subagent_start') {
                                subChatWindow.innerHTML = '<div class="message system-msg"><p>I am the Research Sub-Agent. Starting Investigation...</p></div>';
                                subContextList.innerHTML = '';
                                subTokenCount.textContent = '0 Tokens';
                                subBadge.classList.remove('hidden');
                                if (subStatusText) subStatusText.textContent = '● Starting Research...';
                                currentSubAssistantMsgContainer = null;
                                switchTab('sub');
                                updateSubTyping(data.content);
                            } else if (data.type === 'subagent_thought') {
                                if (subStatusText) subStatusText.textContent = '● Investigating...';
                                showSubTyping('Thinking...');
                            } else if (data.type === 'subagent_thought_stream') {
                                showSubTyping(data.content); 
                            } else if (data.type === 'subagent_stream_chunk') {
                                if (subStatusText) subStatusText.textContent = '● Writing Report...';
                                if (!currentSubAssistantMsgContainer) {
                                    currentSubAssistantMsgContainer = appendSubMessage('', 'agent-msg');
                                }
                                const p = currentSubAssistantMsgContainer.querySelector('p') || document.createElement('p');
                                if (!p.parentElement) currentSubAssistantMsgContainer.appendChild(p);
                                p.textContent += data.content;
                                subChatWindow.scrollTop = subChatWindow.scrollHeight;
                            } else if (data.type === 'subagent_tool_call') {
                                if (subStatusText) subStatusText.textContent = `● Using ${data.tool}...`;
                                currentSubAssistantMsgContainer = null;
                                updateSubTyping(null);
                                appendSubStep(`Sub-Agent 🔎: ${data.tool}`, JSON.stringify(data.args, null, 2));
                                addSubContextItem(`Tool Call: ${data.tool}`, JSON.stringify(data.args), 'assistant', data.tokens || 50, JSON.stringify(data.args, null, 2));
                                updateSubTyping("Executing tool...");
                            } else if (data.type === 'subagent_tool_result') {
                                updateSubTyping(null);
                                appendSubStep(`Sub-Agent Result`, data.result);
                                addSubContextItem(`Tool Result`, data.result.substring(0, 50) + '...', 'tool', data.tokens || 50, data.result);
                            } else if (data.type === 'subagent_final_report') {
                                removeSubTyping();
                                if (subStatusText) subStatusText.textContent = '● Awaiting Task';
                                currentSubAssistantMsgContainer = null;
                                subBadge.classList.add('hidden');
                                switchTab('main');
                            } else if (data.type === 'tool_result') {
                                appendStep(`Tool Result`, data.result);
                                chatMessagesRendered++;
                                addContextItem(`Tool Result`, data.result.substring(0, 50) + '...', 'tool', data.tokens || 50, data.result);
                                if (data.tool.includes('create') || data.tool.includes('delete')) {
                                    showToast(`Calendar Action Confirmed: ${data.tool}`, 'success');
                                }
                            } else if (data.type === 'message') {
                                removeTyping();
                                if (!currentAssistantMsgContainer) {
                                    currentAssistantMsgContainer = appendMessage('', 'agent-msg');
                                    chatMessagesRendered++;
                                }
                                const p = document.createElement('p');
                                p.innerHTML = data.content.replace(/\n/g, '<br>');
                                currentAssistantMsgContainer.appendChild(p);
                                fetchHistory();
                                currentAssistantMsgContainer = null;
                            } else if (data.type === 'error') {
                                removeTyping();
                                appendMessage(`Error: ${data.content}`, 'system-msg');
                            }
                        } catch (parseError) {
                            console.error("Error parsing SSE line:", parseError, line);
                        }
                    }
                }
            }
        } catch (e) {
            if (e.name === 'AbortError') {
                console.log('Fetch aborted');
                appendMessage('Request cancelled.', 'system-msg');
            } else {
                console.error(e);
                appendMessage(`Connection error.`, 'system-msg');
            }
            removeTyping();
        } finally {
            isWaiting = false;
            abortController = null;
            sendBtn.style.display = 'flex';
            stopBtn.style.display = 'none';
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
        const typingIndicator = document.getElementById('typing-indicator');
        if (typingIndicator) {
            chatWindow.insertBefore(div, typingIndicator);
        } else {
            chatWindow.appendChild(div);
        }
        chatWindow.scrollTop = chatWindow.scrollHeight;
        return div;
    }

    function showTyping(label = 'Thinking...') {
        let div = document.getElementById('typing-indicator');
        if (!div) {
            div = document.createElement('div');
            div.id = 'typing-indicator';
            div.className = 'typing';
            chatWindow.appendChild(div);
        }
        div.innerHTML = `<span></span><span></span><span></span> <small>${label}</small>`;
        chatWindow.scrollTop = chatWindow.scrollHeight;
        return div.id;
    }

    function updateTyping(label) {
        if (!label) {
            removeTyping();
            return;
        }
        showTyping(label);
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

        const typingIndicator = document.getElementById('typing-indicator');
        if (typingIndicator) {
            chatWindow.insertBefore(div, typingIndicator);
        } else {
            chatWindow.appendChild(div);
        }

        chatWindow.scrollTop = chatWindow.scrollHeight;
        return div;
    }

    function addContextItem(title, snippet, type, tokens = 100, fullContent = '') {
        const card = document.createElement('div');
        updateContextItem(card, title, snippet, type, tokens, fullContent || snippet);
        contextList.appendChild(card);
        contextList.scrollTop = 0;
    }

    function updateContextItem(card, title, snippet, type, tokens = 100, fullContent = '') {
        const MAX_CONTEXT = appConfig.OLLAMA_NUM_CTX;
        const heightPct = (tokens / MAX_CONTEXT) * 100; // Strictly proportional
        
        // Only update if changed to avoid unnecessary reflows/flicker
        const newClass = `context-card ${type}`;
        if (card.className !== newClass) card.className = newClass;
        
        const newHeight = `${heightPct}%`;
        if (card.style.getPropertyValue('--token-height') !== newHeight) {
            card.style.setProperty('--token-height', newHeight);
        }
        
        const newContent = `<strong>${title}</strong>${snippet.replace(/</g, "&lt;")}`;
        if (card.innerHTML !== newContent) card.innerHTML = newContent;
        
        const newTitle = `${title}: ~${tokens} tokens`;
        if (card.title !== newTitle) card.title = newTitle;

        // Store tokens for total calculation and update display
        const oldTokens = parseInt(card.dataset.tokens || 0);
        card.dataset.tokens = tokens;
        
        // Store full content for modal and add click listener
        card.onclick = () => showContextModal(title, fullContent || snippet);
        
        updateTotalTokenDisplay(tokens - oldTokens);
    }

    let cachedTotalTokens = 0;

    function updateTotalTokenDisplay(diff = 0) {
        if (diff === 0) {
            // Full recount
            const cards = contextList.querySelectorAll('.context-card');
            cachedTotalTokens = 0;
            cards.forEach(card => {
                cachedTotalTokens += parseInt(card.dataset.tokens || 0);
            });
        } else {
            cachedTotalTokens += diff;
        }
        tokenCount.textContent = `${cachedTotalTokens} Tokens`;
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

    // --- SUB-AGENT DOM HELPERS ---

    function showSubTyping(label = 'Thinking...') {
        let div = document.getElementById('sub-typing-indicator');
        if (!div) {
            div = document.createElement('div');
            div.id = 'sub-typing-indicator';
            div.className = 'typing';
            subChatWindow.appendChild(div);
        }
        div.innerHTML = `<span></span><span></span><span></span> <small>${label}</small>`;
        subChatWindow.scrollTop = subChatWindow.scrollHeight;
        return div.id;
    }

    function updateSubTyping(label) {
        if (!label) {
            removeSubTyping();
            return;
        }
        showSubTyping(label);
    }

    function removeSubTyping() {
        const typing = document.getElementById('sub-typing-indicator');
        if (typing) typing.remove();
    }

    function appendSubStep(title, details) {
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

        const typingIndicator = document.getElementById('sub-typing-indicator');
        if (typingIndicator) {
            subChatWindow.insertBefore(div, typingIndicator);
        } else {
            subChatWindow.appendChild(div);
        }

        subChatWindow.scrollTop = subChatWindow.scrollHeight;
        return div;
    }

    function addSubContextItem(title, snippet, type, tokens = 100, fullContent = '') {
        const card = document.createElement('div');
        updateSubContextItem(card, title, snippet, type, tokens, fullContent || snippet);
        subContextList.appendChild(card);
        subContextList.scrollTop = 0;
    }

    function appendSubMessage(text, className) {
        const div = document.createElement('div');
        div.className = `message ${className}`;
        const p = document.createElement('p');
        p.textContent = text;
        div.appendChild(p);
        
        const typingIndicator = document.getElementById('sub-typing-indicator');
        if (typingIndicator) {
            subChatWindow.insertBefore(div, typingIndicator);
        } else {
            subChatWindow.appendChild(div);
        }
        subChatWindow.scrollTop = subChatWindow.scrollHeight;
        return div;
    }

    function updateSubContextItem(card, title, snippet, type, tokens = 100, fullContent = '') {
        const MAX_CONTEXT = appConfig.OLLAMA_NUM_CTX;
        const heightPct = (tokens / MAX_CONTEXT) * 100;
        
        const newClass = `context-card ${type}`;
        if (card.className !== newClass) card.className = newClass;
        
        const newHeight = `${heightPct}%`;
        if (card.style.getPropertyValue('--token-height') !== newHeight) {
            card.style.setProperty('--token-height', newHeight);
        }
        
        const newContent = `<strong>${title}</strong>${snippet.replace(/</g, "&lt;")}`;
        if (card.innerHTML !== newContent) card.innerHTML = newContent;
        
        const newTitle = `${title}: ~${tokens} tokens`;
        if (card.title !== newTitle) card.title = newTitle;

        card.dataset.tokens = tokens;
        card.onclick = () => showContextModal(title, fullContent || snippet);
        
        updateSubTotalTokenDisplay();
    }

    function updateSubTotalTokenDisplay() {
        const cards = subContextList.querySelectorAll('.context-card');
        let total = 0;
        cards.forEach(card => {
            total += parseInt(card.dataset.tokens || 0);
        });
        subTokenCount.textContent = `${total} Tokens`;
    }


});
