import {useEffect,useState,type ReactNode} from 'react'
import {BookOpen,KeyRound} from 'lucide-react'
import {api} from './api'
import {Busy,ErrorBox,useAction} from './ui'

type Mode='login'|'signup'|'recover'
type Account={hosted:boolean,authenticated:boolean,username?:string,open_signup?:boolean}
type AuthResult={authenticated:true,username:string,recovery_code?:string}

export function AccountGate({children}:{children:(username?:string,onSignOut?:()=>void)=>ReactNode}){
  const [account,setAccount]=useState<Account>()
  const [mode,setMode]=useState<Mode>('login')
  const [username,setUsername]=useState('')
  const [password,setPassword]=useState('')
  const [recoveryCode,setRecoveryCode]=useState('')
  const [issued,setIssued]=useState<AuthResult>()
  const [copied,setCopied]=useState(false)
  const action=useAction()
  const title=mode==='signup'?'Create your account':mode==='recover'?'Recover your account':'Sign in to Study Buddy'
  async function load(){setAccount(await api<Account>('/account'))}
  useEffect(()=>{
    void action.run(load)
    const expired=()=>{setAccount(previous=>previous?.hosted?{...previous,authenticated:false}:previous);setMode('login')}
    addEventListener('buddy-session-ended',expired)
    return()=>removeEventListener('buddy-session-ended',expired)
  },[])
  function change(next:Mode){setMode(next);setPassword('');setRecoveryCode('');action.setError('')}
  function signOut(){void action.run(async()=>{
    await api('/account/logout',{})
    setAccount(previous=>previous?{...previous,username:undefined,authenticated:false}:previous)
    setUsername('');change('login')
  })}
  function enter(user:string){setAccount(previous=>previous?{...previous,username:user,authenticated:true}:previous);setIssued(undefined);setCopied(false)}
  if(account?.authenticated&&!issued)return <>{children(account.hosted?account.username:undefined,account.hosted?signOut:undefined)}</>
  return <main className="account-shell"><section className="card panel account-panel">
    <div className="account-brand"><span className="brand-mark"><BookOpen size={22}/></span><span>KTU Study Buddy<small>B.Tech · 2024 scheme</small></span></div>
    <ErrorBox text={action.error}/>
    {!account?<div className="account-intro">{action.busy?<Busy label="Opening Study Buddy…"/>:
      <button className="btn primary" onClick={()=>void action.run(load)}>Try again</button>}</div>:
      issued?<div className="account-intro"><h1>Save your recovery code</h1><p>Store this code somewhere private. You’ll need it to reset your password.</p>
        <pre className="recovery-code">{issued.recovery_code}</pre>
        <div className="actions"><button className="btn secondary" onClick={()=>void action.run(async()=>{await navigator.clipboard.writeText(issued.recovery_code!);setCopied(true)})}>{copied?'Copied':'Copy code'}</button>
        <button className="btn primary" onClick={()=>enter(issued.username)}>I saved my code</button></div>
      </div>:<>
      <div className="account-intro"><h1>{title}</h1><p>{mode==='recover'?'Use the recovery code from account setup to choose a new password.':'Your study library, conversations, and progress are private to your account.'}</p></div>
      <form onSubmit={e=>{e.preventDefault();void action.run(async()=>{
        const body=mode==='recover'?{username,password,recovery_code:recoveryCode.trim()}:{username,password}
        const result=await api<AuthResult>('/account/'+mode,body);setPassword('');setRecoveryCode('')
        if(result.recovery_code)setIssued(result);else enter(result.username)
      })}}>
        <label>Username<input value={username} onChange={e=>setUsername(e.target.value)} autoComplete="username" minLength={3} maxLength={32} autoCapitalize="none" spellCheck={false} required/></label>
        {mode==='recover'&&<label>Recovery code<input value={recoveryCode} onChange={e=>setRecoveryCode(e.target.value)} type="password" autoComplete="off" required/></label>}
        <label>{mode==='recover'?'New password':'Password'}<input value={password} onChange={e=>setPassword(e.target.value)} type="password" autoComplete={mode==='login'?'current-password':'new-password'} minLength={mode==='login'?1:12} maxLength={256} required/></label>
        {mode!=='login'&&<p className="muted">Use at least 12 characters.</p>}
        <button className="btn primary account-submit" disabled={action.busy}><KeyRound size={17}/>{action.busy?'Please wait…':mode==='signup'?'Create account':mode==='recover'?'Reset password':'Sign in'}</button>
      </form>
      <div className="actions account-links">
        {mode!=='login'&&<button className="btn plain" onClick={()=>change('login')}>Sign in</button>}
        {mode==='login'&&account.open_signup&&<button className="btn plain" onClick={()=>change('signup')}>Create account</button>}
        {mode==='login'&&<button className="btn plain" onClick={()=>change('recover')}>Forgot password?</button>}
      </div>
      {mode==='signup'&&<p className="muted">For live AI, attach your own provider key in Settings. The provider receives the prompts and source excerpts used to answer your questions.</p>}
    </>}
  </section></main>
}
