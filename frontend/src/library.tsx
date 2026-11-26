import {useEffect,useRef,useState,type FormEvent} from 'react'
import {BookOpen,FileText,KeyRound,Plus,Upload as UploadIcon} from 'lucide-react'
import {api,localDay,type Outline,type Subject,type Upload} from './api'
import {Busy,Empty,ErrorBox,Heading,useAction} from './ui'
type Coverage={id:string,name:string,topics:{name:string,material:number,notes:number}[]}
type SubjectForm=Omit<Subject,'id'|'outline'>
const initial=():SubjectForm=>({code:'',name:'',semester:3,branch:'',exam_date:localDay()})
export function Library({subjects,selected,select,refresh,create,setCreate,demo}:{subjects:Subject[],selected:number|null,select:(id:number)=>void,refresh:()=>Promise<void>,create:boolean,setCreate:(open:boolean)=>void,demo:()=>void}){
  const subject=subjects.find(item=>item.id===selected)??subjects[0], id=subject?.id
  const [form,setForm]=useState<SubjectForm>(initial)
  const [edit,setEdit]=useState(false)
  const [tag,setTag]=useState('syllabus')
  const [file,setFile]=useState<File>()
  const [files,setFiles]=useState<Upload[]>([])
  const [coverage,setCoverage]=useState<Coverage[]>([])
  const [preview,setPreview]=useState<Outline>()
  const [shown,setShown]=useState<Upload>()
  const [uploadedPaper,setUploadedPaper]=useState(false)
  const activeSubject=useRef(id)
  const fileInput=useRef<HTMLInputElement>(null)
  const action=useAction()
  const syllabus=files.find(item=>item.tag==='syllabus')
  async function load(subjectId:number){
    const [uploads,map]=await Promise.all([api<Upload[]>('/subjects/'+subjectId+'/uploads'),api<Coverage[]>('/subjects/'+subjectId+'/coverage')])
    if(activeSubject.current===subjectId){setFiles(uploads);setCoverage(map)}
  }
  useEffect(()=>{
    activeSubject.current=id
    setPreview(undefined);setShown(undefined);setFiles([]);setCoverage([]);setUploadedPaper(false)
    if(id)void action.run(()=>load(id))
  },[id])
  useEffect(()=>{if(create&&!edit)setForm(initial())},[create])
  function save(e:FormEvent){e.preventDefault();void action.run(async()=>{const saved=await api<Subject>(edit&&id?'/subjects/'+id:'/subjects',form,edit?'PUT':'POST');await refresh();select(saved.id);setEdit(false);setCreate(false)})}
  function review(uploadId:number){void action.run(async()=>{
    const outline=await api<Outline>('/uploads/'+uploadId+'/syllabus-preview')
    if(activeSubject.current===id)setPreview(outline)
  })}
  function upload(e:FormEvent){e.preventDefault();if(!id||!file)return;const data=new FormData();data.append('file',file);data.append('tag',tag)
    void action.run(async()=>{
      const added=await api<Upload>('/subjects/'+id+'/uploads',data)
      if(activeSubject.current!==id)return
      select(id)
      setFile(undefined)
      if(fileInput.current)fileInput.current.value=''
      setUploadedPaper(added.tag==='pyq')
      if(added.metadata.preview){setPreview(added.metadata.preview);setShown(undefined)}
      else setShown(added)
      await load(id)
    })}
  return <>
    <Heading title="Subject library" action={<button className="btn primary" onClick={()=>{setEdit(false);setForm(initial());setCreate(true)}}><Plus size={18}/>Add subject</button>}>Manage syllabi, textbooks, notes, and question papers.</Heading>
    <ErrorBox text={action.error}/>
    {(create||edit)&&<form onSubmit={save} className="card panel"><div className="section-head"><h2>{edit?'Edit subject':'New subject'}</h2></div>
      <div className="field-grid"><label>Course code<input required value={form.code} onChange={e=>setForm({...form,code:e.target.value})}/></label><label>Subject name<input required value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label><label>Semester<input type="number" min="1" max="8" required value={form.semester} onChange={e=>setForm({...form,semester:Number(e.target.value)})}/></label><label>Branch<input required value={form.branch} onChange={e=>setForm({...form,branch:e.target.value})}/></label><label>Exam date<input type="date" required value={form.exam_date} onChange={e=>setForm({...form,exam_date:e.target.value})}/></label></div>
      <div className="actions"><button className="btn primary" disabled={action.busy}>Save subject</button><button type="button" className="btn plain" onClick={()=>{setCreate(false);setEdit(false)}}>Cancel</button></div></form>}
    {!subject?<section className="card panel"><Empty title="Your library is empty" actions={<button className="btn secondary" onClick={demo}>Load sample subject</button>}>Add a subject or explore the sample syllabus.</Empty></section>:<>
      <section className="card panel"><div className="section-head"><h2><BookOpen size={20}/>{subject.name}</h2><button className="btn secondary" onClick={()=>{setEdit(true);setForm(subject)}}>Edit subject</button></div>
        <label>Subject<select value={id} onChange={e=>select(Number(e.target.value))}>{subjects.map(item=><option key={item.id} value={item.id}>{item.code} · {item.name}</option>)}</select></label>
        {!subject.outline.modules.length&&syllabus&&<div className="notice">
          <p>Review and confirm your uploaded syllabus to enable module selection and match past-paper questions.</p>
          <button className="btn secondary" disabled={action.busy} onClick={()=>review(syllabus.id)}>Review uploaded syllabus</button>
        </div>}
        {uploadedPaper&&<p className="notice success" role="status">Past paper uploaded. <a href="#exams">View in Exam Prep</a>.</p>}
        <form className="inline-form" onSubmit={upload}><label>Material type<select value={tag} onChange={e=>setTag(e.target.value)}>{['syllabus','notes','pyq','textbook','other'].map(value=><option key={value} value={value}>{value==='pyq'?'Past paper (PYQ)':value}</option>)}</select></label>
          <label>File<input ref={fileInput} type="file" required accept=".pdf,.docx,.pptx,.txt,.md,.markdown,.jpg,.jpeg,.png,.webp,.tif,.tiff" onChange={e=>setFile(e.target.files?.[0])}/></label><button className="btn primary" disabled={action.busy}><UploadIcon size={16}/>Upload material</button></form>
        {action.busy&&<Busy label="Reading library or extracting pages…"/>}
        <div className="subject-list">{files.map(item=><div className="subject-row" key={item.id}><button className="subject-link" onClick={()=>void action.run(async()=>setShown(await api<Upload>('/uploads/'+item.id)))}><FileText size={19}/><span><strong>{item.name}</strong><small>{item.tag} · {item.pages.length} pages{item.pages.some(page=>page.review)?' · OCR review needed':''}</small></span></button>
          {item.tag==='syllabus'&&<button className="btn secondary" disabled={action.busy} onClick={()=>review(item.id)}>Review syllabus</button>}</div>)}</div>
        {shown&&<DocumentReview key={shown.id} document={shown} busy={action.busy} save={(page,text)=>void action.run(async()=>{
          const revised=await api<Upload>('/uploads/'+shown.id+'/pages/'+page,{text},'PUT')
          setShown(revised)
          if(revised.metadata.preview)setPreview(revised.metadata.preview)
          await load(id!)
        })}/>}
      </section>
      {preview&&<OutlineEditor key={id+JSON.stringify(preview)} source={preview} cancel={()=>setPreview(undefined)} save={outline=>void action.run(async()=>{await api('/subjects/'+id+'/outline',outline,'PUT');if(activeSubject.current===id)setPreview(undefined);await refresh();await load(id!)})} busy={action.busy}/>}
      <section className="card panel"><div className="section-head"><h2>Coverage map</h2><button className="btn secondary" onClick={()=>setPreview(subject.outline)}>Edit outline</button></div>
        {coverage.length?coverage.map(module=><div className="coverage-group" key={module.id}><h3>{module.name}</h3>{module.topics.map(topic=><div className="coverage-row" key={topic.name}><span>{topic.name}</span><span className={'chip '+(topic.material?'ok':'')}>{topic.notes?'Notes available':topic.material?'Material available':'Needs material'}</span></div>)}</div>):<Empty title="No confirmed modules">{syllabus?'Review your uploaded syllabus to add its modules to this subject.':'Upload a syllabus to detect modules, topics, and the exam pattern.'}</Empty>}
      </section></>}
  </>
}
function DocumentReview({document,busy,save}:{document:Upload,busy:boolean,save:(page:number,text:string)=>void}){
  const [drafts,setDrafts]=useState<Record<number,string>>({})
  return <details open className="document-preview">
    <summary>{document.name} · page review</summary>
    {document.metadata.downloadable&&<a className="btn secondary" href={'/api/uploads/'+document.id+'/download'}>Download original</a>}
    {document.pages.map(page=><details key={page.number}>
      <summary>Page {page.number}{page.review?' · Review OCR ('+page.ocr_confidence+'%)':''}</summary>
      {drafts[page.number]===undefined?<><pre>{page.text||'No extracted text on this page.'}</pre><button className="btn secondary" onClick={()=>setDrafts({...drafts,[page.number]:page.text??''})}>Correct page text</button></>:
      <form onSubmit={e=>{e.preventDefault();save(page.number,drafts[page.number])}}>
        <label>Page transcription<textarea rows={6} value={drafts[page.number]} onChange={e=>setDrafts({...drafts,[page.number]:e.target.value})}/></label>
        <div className="actions"><button className="btn primary" disabled={busy}>Save transcription</button><button className="btn plain" type="button" onClick={()=>setDrafts(Object.fromEntries(Object.entries(drafts).filter(([key])=>Number(key)!==page.number)))}>View saved text</button></div>
      </form>}
    </details>)}
  </details>
}

function OutlineEditor({source,save,cancel,busy}:{source:Outline,save:(value:Outline)=>void,cancel:()=>void,busy:boolean}){
  const [outline,setOutline]=useState<Outline>(()=>structuredClone(source))
  const heading=useRef<HTMLHeadingElement>(null)
  useEffect(()=>{heading.current?.focus()},[])
  const examTotal=outline.pattern.parts.reduce((total,part)=>total+part.attempt*part.marks*(part.scope==='paper'?1:outline.modules.length),0)
  function change(fn:(draft:Outline)=>void){const draft=structuredClone(outline);fn(draft);setOutline(draft)}
  const lines=(text:string)=>text.split('\n')
  return <form className="card panel" onSubmit={e=>{e.preventDefault();const cleaned=structuredClone(outline);for(const module of cleaned.modules){module.topics=module.topics.map(topic=>topic.trim()).filter(Boolean);module.outcomes=module.outcomes.map(value=>value.trim()).filter(Boolean)}cleaned.textbooks=cleaned.textbooks.filter(Boolean);cleaned.outcomes=cleaned.outcomes.filter(Boolean);save(cleaned)}}>
    <div className="section-head"><h2 ref={heading} tabIndex={-1}>Review syllabus and assessment</h2></div>
    <p className="notice">{outline.modules.length?
      `${outline.modules.length} modules detected. Check their topics and the exam pattern, then select Confirm syllabus.`:
      'No modules were detected. Add them below, or correct the extracted text in the document preview and try again.'}</p>
    {outline.modules.map((module,index)=><fieldset key={index}><legend>Module {index+1}</legend>
      <div className="field-grid"><label>ID<input required value={module.id} onChange={e=>change(draft=>{draft.modules[index].id=e.target.value})}/></label><label>Name<input required value={module.name} onChange={e=>change(draft=>{draft.modules[index].name=e.target.value})}/></label><label>Hours<input type="number" min="0" step=".5" value={module.hours} onChange={e=>change(draft=>{draft.modules[index].hours=Number(e.target.value)})}/></label></div>
      <label>Topics, one per line<textarea value={module.topics.join('\n')} onChange={e=>change(draft=>{draft.modules[index].topics=lines(e.target.value)})}/></label>
      <label>Outcome IDs, one per line<textarea value={module.outcomes.join('\n')} onChange={e=>change(draft=>{draft.modules[index].outcomes=lines(e.target.value)})}/></label>
      <button type="button" className="btn plain" onClick={()=>change(draft=>{draft.modules.splice(index,1)})}>Remove module</button>
    </fieldset>)}
    <button type="button" className="btn secondary" onClick={()=>change(draft=>{
      let next=1
      while(draft.modules.some(module=>module.id===String(next)))next++
      draft.modules.push({id:String(next),name:'Module '+next,topics:[],outcomes:[],hours:0})
    })}>Add module</button>
    <h3>Exam pattern</h3>
    {!outline.pattern_evidence.length?<p className="notice">Check the proposed pattern against your syllabus.</p>:<details><summary>Detected assessment text</summary><pre>{outline.pattern_evidence.join('\n')}</pre></details>}
    <div className="field-grid">{(['cie','ese','duration_minutes'] as const).map((key,index)=><label key={key}>{['CIE marks','ESE marks','Duration in minutes'][index]}<input type="number" min={key==='cie'?0:1} value={outline.pattern[key]} onChange={e=>change(draft=>{draft.pattern[key]=Number(e.target.value)})}/></label>)}</div>
    {outline.pattern.parts.map((part,index)=><div className="part-editor" key={index}>
      <label>Part<input required value={part.name} onChange={e=>change(draft=>{draft.pattern.parts[index].name=e.target.value})}/></label>
      <label>Applies to<select value={part.scope??'module'} onChange={e=>change(draft=>{draft.pattern.parts[index].scope=e.target.value as 'module'|'paper'})}><option value="module">Each module</option><option value="paper">Whole paper</option></select></label>
      {(['offered','attempt','marks','subdivisions'] as const).map((key,i)=><label key={key}>{['Questions offered','Questions to attempt','Marks / question','Max subdivisions'][i]}<input type="number" min="1" value={part[key]} onChange={e=>change(draft=>{draft.pattern.parts[index][key]=Number(e.target.value)})}/></label>)}
      <button type="button" className="btn secondary" disabled={outline.pattern.parts.length===1} onClick={()=>change(draft=>{draft.pattern.parts.splice(index,1)})}>Remove</button>
    </div>)}
    <button type="button" className="btn secondary" onClick={()=>change(draft=>{draft.pattern.parts.push({name:'C',offered:1,attempt:1,marks:1,subdivisions:1})})}>Add exam part</button>
    {!!outline.modules.length&&examTotal!==outline.pattern.ese&&<p className="notice">The exam parts add up to {examTotal} marks. Adjust them or the ESE total to match your syllabus before confirming.</p>}
    <div className="field-grid"><label>Course outcomes<textarea value={outline.outcomes.join('\n')} onChange={e=>change(draft=>{draft.outcomes=lines(e.target.value)})}/></label><label>Textbooks<textarea value={outline.textbooks.join('\n')} onChange={e=>change(draft=>{draft.textbooks=lines(e.target.value)})}/></label></div>
    <div className="actions"><button className="btn primary" disabled={busy||!outline.modules.length||examTotal!==outline.pattern.ese}>Confirm syllabus</button><button type="button" className="btn plain" onClick={cancel}>Cancel</button></div>
  </form>
}
type Limits={context:number,max_output:number,vision:boolean,structured:boolean,long_context:boolean,requests_per_minute?:number,tokens_per_minute?:number}
type Provider={id:number,kind:string,name:string,model:string,base_url:string,last_four:string,position:number,enabled:boolean,status:string,cooldown:number,calls:number,tokens:number,limits:Limits}
type ProviderForm=Pick<Provider,'kind'|'name'|'model'|'base_url'|'position'|'enabled'|'limits'>&{key:string}
const providerInitial=():ProviderForm=>({kind:'Gemini',name:'',model:'',base_url:'',key:'',position:0,enabled:true,limits:{context:16000,max_output:2200,vision:false,structured:false,long_context:false}})
export function ProviderSettings({status}:{status:{mock:boolean,search_mock:boolean}|null}){
  const [rows,setRows]=useState<Provider[]>([])
  const [kinds,setKinds]=useState<string[]>([])
  const [rowId,setRowId]=useState<number>()
  const [form,setForm]=useState<ProviderForm>(providerInitial)
  const [models,setModels]=useState<string[]>([])
  const [tested,setTested]=useState('')
  const action=useAction()
  async function load(){const data=await api<{providers:Provider[],kinds:string[]}>('/providers');setRows(data.providers);setKinds(data.kinds)}
  useEffect(()=>{void action.run(load)},[])
  function edit(row:Provider){setRowId(row.id);setForm({...row,key:''});setModels([]);setTested('')}
  function reset(){setRowId(undefined);setForm(providerInitial());setModels([]);setTested('')}
  return <>
    <Heading title="Settings">Configure provider priority, model capabilities, and usage limits.</Heading>
    <p className="notice">AI mode: {status?.mock?'Mock · local demonstration':'Live'} · Search: {status?.search_mock?'Mock · no external results':'Tavily'}. Set BUDDY_MOCK or SEARCH_MOCK in .env and restart to change modes. Some free tiers may use submitted data for training. Check pricing and terms before using a provider.</p>
    <ErrorBox text={action.error}/>
    <section className="card panel"><div className="section-head"><h2><KeyRound size={20}/>AI providers</h2><button className="btn secondary" onClick={reset}><Plus size={16}/>New provider</button></div>
      {rows.map(row=><div className="subject-row" key={row.id}><div><strong>{row.position}. {row.name}</strong><small>{row.model} · ••••{row.last_four} · {row.enabled?row.cooldown>Date.now()/1000?'Cooldown until '+new Date(row.cooldown*1000).toLocaleString():row.status:'Disabled · '+row.status} · {row.calls} calls · {row.tokens} estimated tokens</small></div><div className="actions">
        <button className="btn secondary" disabled={action.busy} onClick={()=>edit(row)}>Edit</button>
        <button className="btn secondary" disabled={action.busy} onClick={()=>void action.run(async()=>{edit(row);try{const result=await api<{models:string[],configured_model_found:boolean}>('/providers/'+row.id+'/test',{});setModels(result.models);setTested(result.configured_model_found?'Key accepted and the configured model is listed.':'Key accepted. Choose a listed model if your current model is unavailable.')}finally{await load()}})}>Test / list models</button>
        <button className="btn plain" disabled={action.busy} onClick={()=>{if(confirm('Delete the saved key for '+row.name+'?'))void action.run(async()=>{await api('/providers/'+row.id,undefined,'DELETE');await load();if(row.id===rowId)reset()})}}>Delete</button>
      </div></div>)}
      {!rows.length&&<p className="muted">No API keys have been added.</p>}
    </section>
    <form className="card panel" onSubmit={e=>{e.preventDefault();void action.run(async()=>{const {kind,name,base_url,model,key,position,enabled,limits}=form;await api(rowId?'/providers/'+rowId:'/providers',{kind,name,base_url,model,key,position,enabled,limits},rowId?'PUT':'POST');reset();await load()})}}>
      <div className="section-head"><h2>{rowId?'Edit provider':'Add provider'}</h2></div>
      {tested&&<p className="notice">{tested}</p>}
      <div className="field-grid">
        <label>Provider<select value={form.kind} onChange={e=>setForm({...form,kind:e.target.value})}>{kinds.map(kind=><option key={kind}>{kind}</option>)}</select></label>
        <label>Display name<input value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>
        <label>Model ID<input required list="provider-models" value={form.model} onChange={e=>setForm({...form,model:e.target.value})}/><datalist id="provider-models">{models.map(name=><option value={name} key={name}/>)}</datalist></label>
        <label>API key<input required={!rowId} type="password" autoComplete="new-password" value={form.key} onChange={e=>setForm({...form,key:e.target.value})} placeholder={rowId?'Leave blank to keep saved key':''}/></label>
        {form.kind==='Custom'&&<label>Base URL<input required value={form.base_url} onChange={e=>setForm({...form,base_url:e.target.value})}/></label>}
        <label>Priority (lower first)<input type="number" min="0" max="200" value={form.position} onChange={e=>setForm({...form,position:Number(e.target.value)})}/></label>
        {(['context','max_output'] as const).map((key,i)=><label key={key}>{i?'Maximum output tokens':'Context tokens'}<input type="number" required value={form.limits[key]} onChange={e=>setForm({...form,limits:{...form.limits,[key]:Number(e.target.value)}})}/></label>)}
        {(['requests_per_minute','tokens_per_minute'] as const).map((key,i)=><label key={key}>{i?'Tokens per minute (0 = unset)':'Requests per minute (0 = unset)'}<input type="number" min="0" value={form.limits[key]??0} onChange={e=>setForm({...form,limits:{...form.limits,[key]:Number(e.target.value)}})}/></label>)}
      </div>
      <div className="actions checks"><label><input type="checkbox" checked={form.enabled} onChange={e=>setForm({...form,enabled:e.target.checked})}/>Enabled</label>{(['vision','structured','long_context'] as const).map((key,i)=><label key={key}><input type="checkbox" checked={form.limits[key]} onChange={e=>setForm({...form,limits:{...form.limits,[key]:e.target.checked}})}/>{['Vision','Structured outputs','Long context'][i]}</label>)}</div>
      <button className="btn primary" disabled={action.busy}>Save provider</button>
    </form>
  </>
}
