// reads JSON array of strings on stdin -> prints JSON array of o200k_base token counts
import { encode } from 'gpt-tokenizer/encoding/o200k_base';
let s=''; process.stdin.on('data',d=>s+=d).on('end',()=>{
  const arr = JSON.parse(s);
  console.log(JSON.stringify(arr.map(x => encode(x).length)));
});
