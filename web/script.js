let statusData = {}
let countdownInterval = null
let targetExpiry = null

async function fetchStatus() {
  try {
    const response = await fetch('/api/status')
    if (response.ok) {
      statusData = await response.json()
      updateUI()
    }
  } catch (error) {
    console.error('Failed to fetch status:', error)
  }
}

function updateUI() {
  // 1. Status Badge
  const badge = document.getElementById('status-badge')
  const badgeText = badge.querySelector('.status-text')

  if (statusData.enabled) {
    const egressState = statusData.egress ? statusData.egress.state : 'unverified'
    const isRunning =
      statusData.status === 'running' ||
      (statusData.vpn_connected && statusData.handshake === 'active')
    if (isRunning && egressState === 'not_tunneled') {
      // Explicit warning: tunnel is up/subscription active, but outbound
      // traffic is NOT tunneled (fail-open gap). Surfaced as a distinct state.
      badge.className = 'status-badge warning'
      badgeText.textContent = 'OUTBOUND NOT TUNNELED'
    } else if (isRunning) {
      badge.className = 'status-badge active'
      badgeText.textContent = 'SUBSCRIPTION ACTIVE'
    } else if (statusData.status === 'expired') {
      badge.className = 'status-badge inactive'
      badgeText.textContent = 'SUBSCRIPTION EXPIRED'
    } else if (statusData.status === 'unconfigured') {
      badge.className = 'status-badge inactive'
      badgeText.textContent = 'UNCONFIGURED (Add config in settings)'
    } else {
      badge.className = 'status-badge inactive'
      badgeText.textContent = 'CONNECTING / SYNCING'
    }
  } else {
    badge.className = 'status-badge inactive'
    badgeText.textContent = 'TUNNEL DISABLED (Turn on in settings)'
  }

  // 2. Properties
  document.getElementById('val-public-ip').textContent =
    statusData.public_ip || 'None'
  document.getElementById('val-vpn-port').textContent =
    statusData.vpn_port || 'None'
  document.getElementById('val-pubkey').textContent =
    statusData.pubkey || 'None'
  document.getElementById('val-pubkey').title = statusData.pubkey || 'None'

  document.getElementById('val-octet').textContent =
    statusData.internal_octet || 'Unknown'

  // Footer Version
  if (statusData.version) {
    const versionEl = document.getElementById('footer-version')
    if (versionEl) {
      versionEl.textContent = 'v' + statusData.version
    }
  }
  // Option 3 Security Warning Banner
  const ipv6Banner = document.getElementById('ipv6-warning-banner')
  if (ipv6Banner) {
    ipv6Banner.style.display = statusData.allow_ipv6 ? 'block' : 'none'
  }

  // Outbound egress warning banner: shown when outbound is NOT tunneled
  const egressState = statusData.egress ? statusData.egress.state : 'unverified'
  const egressBanner = document.getElementById('egress-warning-banner')
  if (egressBanner) {
    egressBanner.style.display = egressState === 'not_tunneled' ? 'block' : 'none'
  }

  // 3. Highlighted guide config parameters
  const ipElements = document.querySelectorAll('.highlight-ip')
  const portElements = document.querySelectorAll('.highlight-port')

  ipElements.forEach(
    (el) => (el.textContent = statusData.public_ip || '<TunnelSats IP>'),
  )
  portElements.forEach(
    (el) => (el.textContent = statusData.vpn_port || '<Forwarding Port>'),
  )

  // 4. Expiry / Countdown
  const expiryRaw = document.getElementById('expiry-date-raw')
  const timerEl = document.getElementById('countdown-timer')
  const progressEl = document.getElementById('subscription-progress')

  if (statusData.expires_at && statusData.expires_at !== 'Unknown') {
    const parsedDate = new Date(statusData.expires_at)
    if (!isNaN(parsedDate.getTime())) {
      expiryRaw.textContent = parsedDate.toLocaleString()
      targetExpiry = parsedDate
    } else {
      targetExpiry = null
      expiryRaw.textContent = 'Invalid Expiry Date'
      timerEl.textContent = 'No Active Subscription'
      progressEl.style.width = '0%'
    }
  } else {
    targetExpiry = null
    expiryRaw.textContent = 'Unconfigured / Inactive'
    timerEl.textContent = 'No Active Subscription'
    progressEl.style.width = '0%'
  }

  if (!countdownInterval) {
    countdownInterval = setInterval(() => {
      if (!targetExpiry) return

      const now = new Date()
      const timeDiff = targetExpiry - now

      const tEl = document.getElementById('countdown-timer')
      const pEl = document.getElementById('subscription-progress')
      if (!tEl || !pEl) return

      if (timeDiff <= 0) {
        tEl.textContent = 'Expired'
        tEl.style.color = '#ff7b72'
        pEl.style.width = '0%'
      } else {
        tEl.style.color = ''
        const days = Math.floor(timeDiff / (1000 * 60 * 60 * 24))
        const hours = Math.floor(
          (timeDiff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60),
        )
        const minutes = Math.floor((timeDiff % (1000 * 60 * 60)) / (1000 * 60))
        const seconds = Math.floor((timeDiff % (1000 * 60)) / 1000)

        if (days > 0) {
          tEl.textContent = `${days}d ${hours}h ${minutes}m`
        } else {
          tEl.textContent = `${hours}h ${minutes}m ${seconds}s`
        }

        // Progress Bar (assume 30 days max subscription)
        const maxTerm = 30 * 24 * 60 * 60 * 1000
        const percentage = Math.min(
          100,
          Math.max(0, (timeDiff / maxTerm) * 100),
        )
        pEl.style.width = `${percentage}%`
      }
    }, 1000)
  }
}

function handleCopySuccess(btn, successText) {
  const originalText = btn.textContent
  btn.textContent = successText
  btn.classList.add('copied')
  setTimeout(() => {
    btn.textContent = originalText
    btn.classList.remove('copied')
  }, 1500)
}

function executeCopy(text, btn, successText) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard
      .writeText(text)
      .then(() => {
        handleCopySuccess(btn, successText)
      })
      .catch(() => {
        fallbackCopy(text, btn, successText)
      })
  } else {
    fallbackCopy(text, btn, successText)
  }
}

function fallbackCopy(text, btn, successText) {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.style.position = 'fixed'
  textarea.style.opacity = '0'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    document.execCommand('copy')
    handleCopySuccess(btn, successText)
  } catch (err) {
    console.error('Fallback copy failed', err)
  }
  document.body.removeChild(textarea)
}

function copyText(elementId, btn) {
  const text =
    document.getElementById(elementId).title ||
    document.getElementById(elementId).textContent
  executeCopy(text, btn, 'Copied!')
}

function copyCode(elementId, btn) {
  const text = document.getElementById(elementId).textContent
  const originalText = btn ? btn.textContent.trim() : ''
  const successText = originalText ? originalText.replace(/^Copy\b/, 'Copied') + '!' : 'Copied!'
  executeCopy(text, btn, successText)
}

function switchTab(tabId, btn) {
  // Hide all tab contents
  document.querySelectorAll('.tab-content').forEach((tab) => {
    tab.classList.remove('active')
  })
  // Remove active class from buttons
  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.classList.remove('active')
  })
  // Show selected tab content
  document.getElementById(tabId).classList.add('active')
  // Set active class on button
  btn.classList.add('active')
}

// ─────────────────────────────────────────────
// FAQ Modal
// ─────────────────────────────────────────────

const faqModal = document.getElementById('faq-modal')

function openFaqModal() {
  if (faqModal) {
    faqModal.showModal()
    // Trap focus within modal on open
    faqModal.querySelector('.faq-close-btn').focus()
  }
}

function closeFaqModal() {
  if (faqModal) {
    // Animate out then close
    const inner = faqModal.querySelector('.faq-dialog-inner')
    if (inner) {
      inner.style.animation =
        'faq-slide-in 0.18s cubic-bezier(0.4, 0, 1, 1) reverse both'
      setTimeout(() => {
        faqModal.close()
        inner.style.animation = ''
      }, 160)
    } else {
      faqModal.close()
    }
  }
}

// Close when clicking the backdrop (outside .faq-dialog-inner)
if (faqModal) {
  faqModal.addEventListener('click', (e) => {
    // The dialog element itself is the backdrop area
    if (e.target === faqModal) {
      closeFaqModal()
    }
  })

  // Native Escape key support is built into <dialog>, but we hook it
  // to use our animated close instead of the abrupt browser default
  faqModal.addEventListener('cancel', (e) => {
    e.preventDefault()
    closeFaqModal()
  })
}

// Initial fetch and set interval
fetchStatus()
setInterval(fetchStatus, 15000)
