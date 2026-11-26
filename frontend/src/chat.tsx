import {useEffect,useRef,useState} from 'react'
import {MessageSquare,Paperclip,Plus,Send,Square} from 'lucide-react'
import {api,message,type Proposal,type Scope,type Source,type Subject,type Upload} from './api'
import {Busy,Empty,ErrorBox,Heading,Markdown,ScopeFields,Sources,useAction} from './ui'

type Message={role:'user'|'assistant',content:string,sources?:Source[],provider?:string,estimated_tokens?:number,proposal?:Proposal}
type ChatList={id:string,title:string,scope:Scope}
type History=ChatList&{messages:Message[]}
type Event={type:string,text?:string,message?:string|Message,sources?:Source[]}
const all:Scope={subject_id:null,module:null,topic:null}

export function Chat({subjects,initial,perform}:{subjects:Subject[],initial?:Scope,perform:(proposal:Proposal)=>void}){
  const [list,setList]=useState<ChatList[]>([])
  const [chat,setChat]=useState<History>()
  const [search,setSearch]=useState('')
  const [scope,setScope]=useState(initial??all)
  const [text,setText]=useState('')
  const [marks,setMarks]=useState('')
  const [files,setFiles]=useState<File[]>([])
  const [stream,setStream]=useState<Message>()
  const [status,setStatus]=useState('')
  const [sending,setSending]=useState(false)
  const controller=useRef<AbortController|null>(null)
  const end=useRef<HTMLDivElement>(null)
  const action=useAction()
  async function loadList(term=search){setList(await api<ChatList[]>('/chats?q='+encodeURIComponent(term)))}
  useEffect(()=>{void action.run(()=>loadList());return()=>controller.current?.abort()},[])
  useEffect(()=>{const timer=setTimeout(()=>void loadList(search).catch(e=>action.setError(message(e))),250);return()=>clearTimeout(timer)},[search])
  useEffect(()=>{if(sending)end.current?.scrollIntoView({block:'nearest'})},[stream,sending])
  async function open(id:string){const next=await api<History>('/chats/'+id);setChat(next);setScope(next.scope);setStream(undefined);setFiles([]);setText('')}
  function reset(){setChat(undefined);setStream(undefined);setScope(initial??all);setFiles([]);setText('');setStatus('')}
  async function send(regenerate=false){
    if(sending||(!regenerate&&!text.trim()&&!files.length))return
    setSending(true);setStatus('');setStream(undefined);action.setError('')
    const abort=new AbortController();controller.current=abort
    let id=chat?.id
    try {
      if(!id){const next=await api<History>('/chats',scope);id=next.id;setChat(next)}
      const attachments=[]
      if(!regenerate)for(const file of files){const data=new FormData();data.append('file',file);attachments.push((await api<Upload>('/chats/'+id+'/attachments',data)).id)}
      const prompt=text.trim()||'Explain the attached material.'
      if(!regenerate){
        setChat(previous=>previous?{...previous,messages:[...previous.messages,{role:'user',content:prompt}]}:previous)
        setText('');setFiles([])
      } else {
        setChat(previous=>previous?.messages.at(-1)?.role==='assistant'?{...previous,messages:previous.messages.slice(0,-1)}:previous)
      }
      const response=await fetch('/api/chats/'+id+'/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...scope,text:prompt,marks:marks?Number(marks):null,attachments,regenerate}),signal:abort.signal})
      if(!response.ok||!response.body)throw new Error((await response.json().catch(()=>({}))).detail??'The answer could not start.')
      const reader=response.body.getReader(),decoder=new TextDecoder()
      let buffer='',draft:Message={role:'assistant',content:'',sources:[]}
      function apply(event:Event){
        if(event.type==='sources')draft={...draft,sources:event.sources}
        if(event.type==='delta')draft={...draft,content:draft.content+(event.text??'')}
        if(event.type==='reset')draft={...draft,content:''}
        if(event.type==='status')setStatus(String(event.message))
        if(event.type==='error')throw new Error(String(event.message))
        if(event.type==='done')draft=event.message as Message
        setStream(draft)
      }
      while(true){
        const chunk=await reader.read()
        if(chunk.done)break
        buffer+=decoder.decode(chunk.value,{stream:true})
        let boundary=buffer.indexOf('\n\n')
        while(boundary>=0){
          const frame=buffer.slice(0,boundary)
          buffer=buffer.slice(boundary+2)
          if(frame.startsWith('data: '))apply(JSON.parse(frame.slice(6)))
          boundary=buffer.indexOf('\n\n')
        }
      }
      setChat(await api<History>('/chats/'+id));setStream(undefined);await loadList()
    } catch(e){
      if(!abort.signal.aborted)action.setError(message(e));else setStatus('Response stopped.')
      if(id){setChat(await api<History>('/chats/'+id).catch(()=>undefined));await loadList().catch(()=>{})}
    } finally {controller.current=null;setSending(false)}
  }
  return <>
    <Heading title="Study chat">Ask a question, work through a numerical, or bring in a photo or file.</Heading>
    <ErrorBox text={action.error}/>
    <div className="chat-layout">
      <aside className="card panel conversation-list">
        <button className="btn primary" disabled={sending} onClick={reset}><Plus size={17}/>New chat</button>
        <label>Search chats<input type="search" value={search} onChange={e=>setSearch(e.target.value)}/></label>
        {list.map(item=><button key={item.id} className={chat?.id===item.id?'conversation current':'conversation'} disabled={sending} onClick={()=>void action.run(()=>open(item.id))}><MessageSquare size={16}/>{item.title}</button>)}
      </aside>
      <section className="card panel chat-main">
        <div className="section-head"><h2>{chat?.title??'New conversation'}</h2>{chat&&<button className="btn secondary" disabled={sending} onClick={()=>{const title=prompt('Conversation title',chat.title);if(title?.trim())void action.run(async()=>{setChat(await api('/chats/'+chat.id,{title:title.trim()},'PATCH'));await loadList()})}}>Rename</button>}</div>
        <ScopeFields subjects={subjects} scope={scope} onChange={setScope} allowAll/>
        {!chat?.messages.length&&!stream&&<Empty title="What are you studying?">Ask for a step-by-step explanation or search your uploaded material.</Empty>}
        <div className="messages">
          {[...(chat?.messages??[]),...(stream?[stream]:[])].map((item,index)=><article className={'bubble '+item.role} key={index}>
            <div className="message-label">{item.role==='user'?'You':item.provider??'Study Buddy'}{item.estimated_tokens?' · ~'+item.estimated_tokens+' tokens':''}</div>
            <Markdown value={item.content} sources={item.sources}/><Sources sources={item.sources}/>
            {item.proposal&&<button className="btn secondary" onClick={()=>{const proposal=item.proposal!;if(!proposal.requires_confirmation||confirm('Open the study action for review?'))perform(proposal)}}>{item.proposal.requires_confirmation?'Review action':'Open study tool'}</button>}
          </article>)}
          <div ref={end}/>
        </div>
        {status&&<p className="muted" role="status">{status}</p>}
        {sending&&!stream&&<Busy label="Preparing answer…"/>}
        <form onSubmit={e=>{e.preventDefault();void send()}} className="composer">
          <label>Message<textarea value={text} onChange={e=>setText(e.target.value)} placeholder="Ask anything about your course…" rows={3}/></label>
          <div className="actions"><label className="btn secondary attach"><Paperclip size={16}/>Attach<input type="file" multiple onChange={e=>setFiles(Array.from(e.target.files??[]).slice(0,5))}/></label>
          <select aria-label="Answer length" value={marks} onChange={e=>setMarks(e.target.value)}><option value="">Answer length</option><option value="3">Answer as 3 marks</option><option value="9">Answer as 9 marks</option></select>
          {sending?<button type="button" className="btn secondary" onClick={()=>controller.current?.abort()}><Square size={15}/>Stop</button>:<button className="btn primary" disabled={!text.trim()&&!files.length}><Send size={16}/>Send</button>}
          {chat?.messages.length&&!sending?<button type="button" className="btn plain" onClick={()=>void send(true)}>Regenerate</button>:null}</div>
          {!!files.length&&<p className="muted">{files.map(file=>file.name).join(', ')} <button className="btn plain" type="button" onClick={()=>setFiles([])}>Clear</button></p>}
        </form>
        <details><summary>Study commands</summary><p className="muted">Use /search, /web, /notes, /quiz, /predictions, /grade or /plan. Set the subject or topic first.</p></details>
      </section>
    </div>
  </>
}
