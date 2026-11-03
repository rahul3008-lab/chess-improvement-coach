const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createController} = require('../src/chesscoach/dashboard/board/board.js');

function setup(pieces, legal) {
  const events = [], paints = [];
  let sequence = 0, board;
  board = createController(value => events.push(value), () => {
    if (board) paints.push({...board.state.pieces});
  }, () => `move-${++sequence}`);
  const sprites = Object.fromEntries([... 'PNBRQKpnbrqk'].map(symbol => [symbol, {symbol}]));
  const args = {fen:'original-position', pieces:Object.fromEntries(Object.entries(pieces).map(([sq,symbol]) => [sq,sprites[symbol]])),
    sprites, legal, ack:null, orientation:'white'};
  board.update(args);
  return {board, events, paints, args};
}

test('click piece then legal destination paints immediately and sends exactly one move', () => {
  const {board,events,paints} = setup({e2:'P'}, ['e2e3','e2e4']);
  board.click('e2');
  assert.deepEqual(board.destinations(), ['e3','e4']);
  assert.equal(events.length, 0);
  assert.equal(board.click('e4'), true);
  assert.equal(paints.at(-1).e4.symbol, 'P');
  assert.equal(board.state.pieces.e2, undefined);
  assert.deepEqual(events, [{move:'e2e4',fen:'original-position',event_id:'move-1'}]);
  board.click('e4'); board.click('e3');
  assert.equal(events.length, 1, 'do not send duplicate moves while awaiting the server');
});

test('illegal destination does nothing and the original piece stays selected', () => {
  const {board,events} = setup({e2:'P'}, ['e2e3','e2e4']);
  board.click('e2');
  assert.equal(board.click('e5'), false);
  assert.equal(board.state.selected, 'e2');
  assert.equal(board.state.pieces.e2.symbol, 'P');
  assert.equal(events.length, 0);
  board.click('e4');
  assert.equal(events[0].move, 'e2e4');
});

test('unrelated rerenders preserve selection and optimistic placement until acknowledgement', () => {
  const {board,args} = setup({e2:'P'}, ['e2e4']);
  board.click('e2'); board.update({...args});
  assert.equal(board.state.selected, 'e2');
  board.click('e4'); board.update({...args});
  assert.equal(board.state.pieces.e4.symbol, 'P');
  assert.equal(board.state.pending, 'move-1');
});

test('wrong-answer acknowledgement resets the same position and accepts a retry', () => {
  const {board,args,events} = setup({e2:'P'}, ['e2e3','e2e4']);
  board.click('e2'); board.click('e3');
  board.update({...args, ack:'move-1', feedback:'wrong'});
  assert.equal(board.state.pieces.e2.symbol, 'P');
  assert.equal(board.state.pieces.e3, undefined);
  assert.equal(board.state.pending, null);
  board.click('e2'); board.click('e4');
  assert.equal(events[1].move, 'e2e4');
  assert.equal(events[1].event_id, 'move-2');
});

test('engine response replaces optimistic board with its authoritative reply', () => {
  const {board,args} = setup({e2:'P', e7:'p'}, ['e2e4']);
  board.click('e2'); board.click('e4');
  board.update({...args,fen:'after-e4-e5',ack:'move-1',pieces:{e4:{symbol:'P'},e5:{symbol:'p'}}});
  assert.equal(board.state.pending, null);
  assert.equal(board.state.pieces.e5.symbol, 'p');
});

test('castling preview moves the rook too', () => {
  const {board} = setup({e1:'K', h1:'R'}, ['e1g1']);
  board.click('e1'); board.click('g1');
  assert.equal(board.state.pieces.g1.symbol, 'K');
  assert.equal(board.state.pieces.f1.symbol, 'R');
  assert.equal(board.state.pieces.h1, undefined);
});

test('en passant preview removes the captured pawn', () => {
  const {board} = setup({e5:'P', d5:'p'}, ['e5d6']);
  board.click('e5'); board.click('d6');
  assert.equal(board.state.pieces.d6.symbol, 'P');
  assert.equal(board.state.pieces.d5, undefined);
});

test('promotion uses the selected piece for white and black', () => {
  for (const [from,to,pawn,expected] of [['a7','a8','P','N'],['h2','h1','p','n']]) {
    const {board,events} = setup({[from]:pawn}, [from+to+'q', from+to+'n']);
    board.click(from); board.click(to,'n');
    assert.equal(board.state.pieces[to].symbol, expected);
    assert.equal(events[0].move, from+to+'n');
  }
});

test('review boards cannot send moves', () => {
  const {board,args,events} = setup({e2:'P'}, ['e2e4']);
  board.update({...args,disabled:true});
  board.click('e2'); board.click('e4');
  assert.equal(events.length, 0);
});
