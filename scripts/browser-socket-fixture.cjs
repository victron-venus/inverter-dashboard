/** Track mocked sockets and complete the client's close handshake. */
function trackMockSocket(socket, sockets) {
  sockets.add(socket)
  socket.onClose((code, reason) => {
    // A custom onClose disables Playwright's automatic close forwarding. The
    // acknowledgement can trigger this callback again, so remove first.
    if (sockets.delete(socket)) {
      // Preserve valid client-supplied metadata, not reserved synthetic codes.
      const options = code === 1000 || (code >= 3000 && code <= 4999) ? { code, reason } : {}
      void socket.close(options)
    }
  })
}

module.exports = { trackMockSocket }
