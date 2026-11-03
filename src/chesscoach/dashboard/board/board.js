/* Local click-to-move state. The server still validates and grades every move. */
(function (root) {
  function createController(onMove, onChange, makeId) {
    let args = {}, fen = null, ack = null;
    const state = {pieces: {}, selected: null, pending: null, lastMove: null};
    const ownPiece = square => (args.legal || []).some(move => move.startsWith(square));
    function update(next) {
      const reset = fen !== next.fen || (state.pending && next.ack === state.pending);
      args = next;
      if (reset || fen === null) {
        state.pieces = {...next.pieces};
        state.selected = null;
        state.pending = null;
        state.lastMove = null;
      } else if (!state.pending && ack !== next.ack) {
        state.pieces = {...next.pieces};
      }
      fen = next.fen;
      ack = next.ack;
      onChange();
    }
    function click(square, promotion = 'q') {
      if (args.disabled || state.pending) return false;
      const options = (args.legal || []).filter(move => move.slice(0, 2) === state.selected && move.slice(2, 4) === square);
      if (!options.length) {
        if (ownPiece(square)) { state.selected = square; onChange(); }
        return false; // Illegal destinations leave both the board and selection untouched.
      }
      const move = options.find(value => value.length === 4 || value[4] === promotion) || options[0];
      const from = move.slice(0, 2), to = move.slice(2, 4), piece = state.pieces[from];
      const pieces = {...state.pieces};
      if (piece.symbol.toLowerCase() === 'p' && from[0] !== to[0] && !pieces[to]) delete pieces[to[0] + from[1]];
      delete pieces[from];
      pieces[to] = piece;
      if (piece.symbol.toLowerCase() === 'k' && Math.abs(from.charCodeAt(0) - to.charCodeAt(0)) === 2) {
        const rookFrom = (to[0] === 'g' ? 'h' : 'a') + from[1];
        const rookTo = (to[0] === 'g' ? 'f' : 'd') + from[1];
        pieces[rookTo] = pieces[rookFrom]; delete pieces[rookFrom];
      }
      if (move.length === 5) {
        const symbol = piece.symbol === piece.symbol.toUpperCase() ? move[4].toUpperCase() : move[4];
        pieces[to] = args.sprites[symbol];
      }
      const event = {move, fen, event_id: makeId()};
      state.pieces = pieces; state.lastMove = move; state.selected = null; state.pending = event.event_id;
      onChange(); // Paint immediately, before the Python round trip.
      onMove(event);
      return true;
    }
    function destinations() {
      return (args.legal || []).filter(move => move.startsWith(state.selected || '!')).map(move => move.slice(2, 4));
    }
    return {state, update, click, destinations, args: () => args};
  }
  root.ChessCoachBoard = {createController};
  if (typeof module !== 'undefined') module.exports = root.ChessCoachBoard;
})(typeof window === 'undefined' ? globalThis : window);
