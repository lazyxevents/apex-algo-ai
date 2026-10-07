self.addEventListener('push', event => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { title: 'APEX Trade Alert', body: event.data ? event.data.text() : 'Trade event received.' }
  }

  event.waitUntil((async () => {
    const windows = await clients.matchAll({ type: 'window', includeUncontrolled: true })
    const hasOpenDashboard = windows.length > 0
    const title = data.title || 'APEX Trade Alert'
    const options = {
      body: data.body || 'A new paper trade was executed.',
      icon: '/favicon.ico',
      badge: '/favicon.ico',
      tag: data.tag || 'apex-trade-alert',
      renotify: data.renotify !== false,
      requireInteraction: data.requireInteraction !== false,
      // If APEX is open, the page plays its own soft chime.
      // If completely closed, let the browser/OS use its default notification sound.
      silent: hasOpenDashboard,
      timestamp: data.timestamp || Date.now(),
      data: {
        url: data.url || '/',
        tradeId: data.tradeId || null,
        type: data.type || 'trade.opened',
      },
    }
    await self.registration.showNotification(title, options)
  })())
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  const targetUrl = new URL(event.notification.data?.url || '/', self.location.origin).href

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      for (const client of windowClients) {
        if ('focus' in client) {
          if ('navigate' in client && client.url !== targetUrl) {
            return client.navigate(targetUrl).then(() => client.focus())
          }
          return client.focus()
        }
      }
      return clients.openWindow ? clients.openWindow(targetUrl) : undefined
    })
  )
})
