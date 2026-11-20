import{api}from'./api.js';
export const state={user:null,data:null,risk:null};
export async function refresh(){
  const[data,risk]=await Promise.all([api('/archive'),api('/stocktakes/risk')]);
  state.data=data;state.risk=risk;
  return state;
}
