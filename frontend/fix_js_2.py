import sys

js_code = r"""
const messages = document.getElementById('messages');
const input = document.getElementById('messageInput');
const typing = document.getElementById('typing');
const flowGrid = document.getElementById('flowGrid');
const inputContext = document.getElementById('inputContext');
const contactModal = document.getElementById('contactModal');
const toast = document.getElementById('toast');
const agentName = document.getElementById('agentName');
const agentRole = document.getElementById('agentRole');
const BACKEND_URL = 'http://127.0.0.1:5000';

input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 110) + 'px';
});

input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
});

function escapeHTML(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/'/g, '&#39;')
        .replace(/"/g, '&quot;')
        .replace(/\n/g, '<br>');
}

function scrollMessages() {
    setTimeout(() => {
        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
        messages.scrollTo({ top: messages.scrollHeight, behavior: 'smooth' });
    }, 50);
}

function showTyping() {
    typing.classList.add('active');
    scrollMessages();
}

function hideTyping() {
    typing.classList.remove('active');
}

function addUserMessage(text) {
    const row = document.createElement('div');
    row.className = 'message-row user';
    row.innerHTML = `
        <div class="message user-message">
            ${escapeHTML(text)}
        </div>
    `;
    messages.insertBefore(row, typing);
    scrollMessages();
}

function addBotMessage(text, actions = []) {
    const row = document.createElement('div');
    row.className = 'message-row';
    
    let actionHTML = '';
    if (actions.length > 0) {
        actionHTML = `
        <div class="chat-actions">
            ${actions.map(action => `
                <button class="chat-action" onclick="quickTopic('${action.replace(/'/g, "\\'")}')">${escapeHTML(action)}</button>
            `).join('')}
        </div>
        `;
    }

    row.innerHTML = `
        <div class="message-avatar">✦</div>
        <div class="message bot-message">
            ${text}
            ${actionHTML}
        </div>
    `;
    messages.insertBefore(row, typing);
    scrollMessages();
}

function sendMessage() {
    const text = input.value.trim();
    if (!text) return;
    
    addUserMessage(text);
    input.value = '';
    input.style.height = '28px';
    
    processMessage(text);
}

function quickTopic(topic) {
    addUserMessage(topic);
    processMessage(topic);
}

async function processMessage(text) {
    showTyping();
    try {
        const response = await fetch(`${BACKEND_URL}/api/v1/reconcile`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: text })
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        hideTyping();
        
        if (data.response_type === 'HUMAN_HANDOFF') {
             const cardHTML = `
               <div style="border:1px solid #c084fc;border-radius:var(--radius-sm);overflow:hidden;margin-top:6px;background:white;box-shadow:var(--shadow-sm);">
                 <div style="background:#faf5ff;padding:12px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid #e9d5ff;">
                   <span style="font-size:18px;">📞</span>
                   <strong style="color:#7e22ce;font-size:13px;">Support Connection</strong>
                 </div>
                 <div style="padding:14px;display:flex;flex-direction:column;gap:12px;align-items:center;text-align:center;">
                   <p style="font-size:12px;color:var(--text);line-height:1.5;margin-bottom:4px;">${escapeHTML(data.message)}</p>
                   <a href="mailto:support@settleassist.com" style="display:inline-block;padding:10px 16px;background:#9333ea;color:white;text-decoration:none;font-size:12px;font-weight:600;border-radius:8px;transition:0.2s;">Contact Support Team</a>
                 </div>
               </div>`;
             addBotMessage(cardHTML);
        } else if (data.message && !data.payload) {
             addBotMessage(escapeHTML(data.message));
        } else if (data.payload) {
             const inv = data.payload.investigation_summary || {};
             const fin = data.payload.financial_audit || {};
             const cardHTML = `
               <div style="border:1px solid var(--danger);border-radius:var(--radius-sm);overflow:hidden;margin-top:6px;">
                 <div style="background:var(--danger-bg);padding:12px 14px;display:flex;align-items:center;gap:8px;border-bottom:1px solid #fecaca;">
                   <span style="font-size:16px;">⚠️</span>
                   <strong style="color:var(--danger);font-size:13px;">FinOps Escalation Required</strong>
                   <span style="margin-left:auto;font-size:10px;color:var(--text-secondary);font-weight:600;">${escapeHTML(data.payload.escalation_tier || '')}</span>
                 </div>
                 <div style="padding:14px;display:flex;flex-direction:column;gap:10px;">
                   <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
                     <div style="background:var(--surface-soft);border-radius:8px;padding:10px;">
                       <span style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.6px;display:block;">Order ID</span>
                       <strong style="font-size:11px;font-family:monospace;">${escapeHTML(inv.order_id || 'N/A')}</strong>
                     </div>
                     <div style="background:var(--surface-soft);border-radius:8px;padding:10px;">
                       <span style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.6px;display:block;">Payment ID</span>
                       <strong style="font-size:11px;font-family:monospace;">${escapeHTML(inv.payment_id || 'N/A')}</strong>
                     </div>
                   </div>
                   <div style="background:var(--surface-soft);border-radius:8px;padding:10px;">
                     <span style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.6px;display:block;margin-bottom:4px;">Failure Stage</span>
                     <span style="font-size:11px;font-weight:600;color:var(--warning);">${escapeHTML(inv.failure_stage || 'UNKNOWN')}</span>
                   </div>
                   <div style="background:var(--surface-soft);border-radius:8px;padding:10px;">
                     <span style="font-size:9px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.6px;display:block;margin-bottom:4px;">Root Cause</span>
                     <span style="font-size:11px;line-height:1.5;">${escapeHTML(inv.root_cause || data.payload.investigation_summary?.root_cause || 'See audit log')}</span>
                   </div>
                   <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:10px;">
                     <span style="font-size:9px;color:var(--primary);text-transform:uppercase;letter-spacing:.6px;display:block;margin-bottom:4px;font-weight:700;">Recommended Action</span>
                     <span style="font-size:11px;line-height:1.5;">${escapeHTML(data.payload.recommended_action || 'Contact FinOps team')}</span>
                   </div>
                 </div>
               </div>`;
             addBotMessage("Our system has flagged this transaction for specialist review. Here are the details:");
             const lastRow = messages.lastElementChild;
             if (lastRow) {
                 const botMessageDiv = lastRow.querySelector('.bot-message');
                 if(botMessageDiv) botMessageDiv.insertAdjacentHTML('beforeend', cardHTML);
             }
        } else if (data.error) {
             addBotMessage(`Error: ${escapeHTML(data.error)}`);
        } else {
             addBotMessage(escapeHTML(JSON.stringify(data)));
        }
    } catch (e) {
        hideTyping();
        addBotMessage("Sorry, I couldn't reach the backend server. Please make sure it's running.");
        console.error("Backend error:", e);
    }
}

function selectMenu(element, type) {
    document.querySelectorAll('.menu-item').forEach(item => item.classList.remove('active'));
    element.classList.add('active');
    if (type === 'transaction') quickTopic('Check my transaction status');
    if (type === 'settlement') quickTopic('My settlement hasn\\'t arrived');
}

function startInvestigation(mode) {
    flowGrid.style.display = 'none';
    agentName.textContent = 'Anaya';
    agentRole.textContent = 'Settlement Intelligence';
    const prompts = {
        general: "Let's start with the reference you have. Enter a Transaction ID, Payment ID, Settlement ID or Payout ID.",
        amount: "Let's investigate the amount difference. Enter the Transaction ID or Payment ID you want to check.",
        bank: "Let's trace the missing bank credit. Enter a Transaction ID, Settlement ID or Payout ID.",
        delay: "Let's check whether the settlement is actually delayed. Enter a Transaction ID or Settlement ID."
    };
    addBotMessage(prompts[mode] || "Enter a reference ID to begin.");
    input.focus();
}

function newConversation() {
    window.location.reload();
}

function openContactModal() {
    contactModal.classList.add('active');
}

function closeContactModal() {
    contactModal.classList.remove('active');
}

function startAgentFromModal() {
    closeContactModal();
    flowGrid.style.display = 'none';
    agentName.textContent = 'Support Desk';
    agentRole.textContent = 'Human support request';
    addBotMessage("Sure. Tell me what happened in your own words.<br><br>If you have a reference ID, include it as well.<br><br>Once I have the issue summary, I'll create a support ticket and provide your callback window.");
    input.focus();
}

function showToast(message) {
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
}

contactModal.addEventListener('click', event => {
    if (event.target === contactModal) closeContactModal();
});
"""

import codecs

try:
    with codecs.open('c:/Users/Rishi/Documents/GitHub/Root.sys/frontend/index.html', 'r', 'utf-8') as f:
        content = f.read()
    
    script_start = content.find('<script>')
    script_end = content.rfind('</script>')
    
    if script_start != -1 and script_end != -1:
        html_top = content[:script_start + len('<script>')]
        html_bottom = content[script_end:]
        
        with codecs.open('c:/Users/Rishi/Documents/GitHub/Root.sys/frontend/index.html', 'w', 'utf-8') as f:
            f.write(html_top + '\n' + js_code + '\n' + html_bottom)
        print("SUCCESS")
    else:
        print("FAILED: Tags not found")
except Exception as e:
    print(f"FAILED: {e}")
