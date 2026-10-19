const assert=require('node:assert/strict');
const view=require('../app/static/transfer_view.js');
assert.equal(view.group({state:'queued'}),'waiting');
assert.equal(view.group({state:'verifying'}),'waiting');
assert.equal(view.group({state:'complete'}),'history');
assert.equal(view.group({state:'failed'}),'attention');
assert.equal(view.group({state:'complete',error:'source retained'}),'attention');
const html=view.render([{id:3,title:'<script>bad</script>',state:'failed',attention_kind:'collision',error:'Destination already exists'}]);
assert(html.includes('Needs Attention (1)'));
assert(html.includes('resolve the collision'));
assert(html.includes('data-retry="3"'));
assert(html.includes('&lt;script&gt;'));
assert(!html.includes('<script>'));
assert(view.render([]).includes('History (0)'));
console.log('Transfer view regression tests passed');

const unknown=view.render([{id:90,title:'Unknown length',state:'verifying',bytes_total:0}]);assert(!unknown.includes('aria-valuenow='),'unknown lengths must not announce a measured percentage');assert(unknown.includes('Verifying complete destination'));
const invalid=view.render([{id:91,title:'Negative progress',state:'copying',bytes_total:10,bytes_copied:-50}]);assert(invalid.includes('aria-valuenow="0"'));
