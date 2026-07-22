import { useCallback, useEffect, useRef, useState } from 'react'

interface BrowserSpeechRecognitionEvent {
  results: ArrayLike<ArrayLike<{ transcript: string }>>
}

interface BrowserSpeechRecognitionErrorEvent {
  error: string
}

interface BrowserSpeechRecognition {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null
  onerror: ((event: BrowserSpeechRecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}

type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition

interface SpeechRecognitionWindow extends Window {
  SpeechRecognition?: BrowserSpeechRecognitionConstructor
  webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor
}

function recognitionConstructor(): BrowserSpeechRecognitionConstructor | undefined {
  if (typeof window === 'undefined') return undefined
  const speechWindow = window as SpeechRecognitionWindow
  return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition
}

export interface SpeechRecognitionHook {
  listening: boolean
  supported: boolean
  start: () => void
  stop: () => void
}

export function useSpeechRecognition(onResult: (text: string) => void): SpeechRecognitionHook {
  const [listening, setListening] = useState(false)
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null)
  const onResultRef = useRef(onResult)
  onResultRef.current = onResult
  const supported = Boolean(recognitionConstructor())

  const stop = useCallback(() => {
    const recognition = recognitionRef.current
    if (!recognition) return
    recognition.stop()
    setListening(false)
  }, [])

  const start = useCallback(() => {
    const SpeechRecognitionAPI = recognitionConstructor()
    if (!SpeechRecognitionAPI || recognitionRef.current) return

    const recognition = new SpeechRecognitionAPI()
    recognition.lang = 'zh-CN'
    recognition.interimResults = false
    recognition.continuous = false
    recognition.onresult = (event) => {
      const fragments: string[] = []
      for (let index = 0; index < event.results.length; index += 1) {
        const transcript = event.results[index]?.[0]?.transcript?.trim()
        if (transcript) fragments.push(transcript)
      }
      const text = fragments.join(' ').trim()
      if (text) onResultRef.current(text)
    }
    recognition.onerror = (event) => {
      console.warn('语音识别错误:', event.error)
      setListening(false)
    }
    recognition.onend = () => {
      recognitionRef.current = null
      setListening(false)
    }

    recognitionRef.current = recognition
    try {
      recognition.start()
      setListening(true)
    } catch (error) {
      recognitionRef.current = null
      setListening(false)
      console.warn('无法启动语音识别:', error)
    }
  }, [])

  useEffect(() => () => {
    const recognition = recognitionRef.current
    if (!recognition) return
    recognition.onresult = null
    recognition.onerror = null
    recognition.onend = null
    recognition.abort()
    recognitionRef.current = null
  }, [])

  return { listening, supported, start, stop }
}
