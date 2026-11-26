export async function api<T>(path: string, body?: unknown, method?: string): Promise<T> {
  const form = body instanceof FormData
  const response = await fetch('/api' + path, {
    method: method ?? (body === undefined ? 'GET' : 'POST'),
    headers: body !== undefined && !form ? {'Content-Type':'application/json'} : {},
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  })
  if (!response.ok) {
    const issue = await response.json().catch(() => ({}))
    const detail = Array.isArray(issue.detail) ? issue.detail.map((item: {msg: string}) => item.msg).join(', ') : issue.detail
    throw new Error(detail ?? 'Request failed (' + response.status + ')')
  }
  return response.json()
}
export function message(error: unknown) { return error instanceof Error ? error.message : String(error) }
export function localDay() {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset()*60000).toISOString().slice(0,10)
}
export type Part = {name:string,scope?:'module'|'paper',offered:number,attempt:number,marks:number,subdivisions:number}
export type Module = {id:string,name:string,hours:number,topics:string[],outcomes:string[]}
export type Outline = {modules:Module[],outcomes:string[],textbooks:string[],pattern_evidence:string[],pattern:{cie:number,ese:number,duration_minutes:number,parts:Part[]}}
export type Subject = {id:number,code:string,name:string,semester:number,branch:string,exam_date:string,outline:Outline}
export type Source = {label:string,name:string,page?:number,url:string,tag:string}
export type Upload = {id:number,name:string,tag:string,metadata:{preview?:Outline,year?:number,session?:string,scheme?:number,url?:string,downloadable?:boolean},pages:{number:number,review:boolean,ocr_confidence:number|null,text?:string}[]}
export type Scope = {subject_id:number|null,module:string|null,topic:string|null}
export type Proposal = {tool:string,requires_confirmation:boolean,subject_id:number|null,module:string|null,topic:string|null,argument:string}
