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

function detachRecognition(recognition: BrowserSpeechRecognition, abort: boolean) {
  recognition.onresult = null
  recognition.onerror = null
  recognition.onend = null
  if (abort) {
    try {
      recognition.abort()
    } catch (error) {
      console.warn('无法中止语音识别:', error)
    }
  }
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

  const finish = useCallback((recognition: BrowserSpeechRecognition, abort = false) => {
    if (recognitionRef.current !== recognition) return
    recognitionRef.current = null
    detachRecognition(recognition, abort)
    setListening(false)
  }, [])

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
      if (recognitionRef.current !== recognition) return
      const fragments: string[] = []
      for (let index = 0; index < event.results.length; index += 1) {
        const transcript = event.results[index]?.[0]?.transcript?.trim()
        if (transcript) fragments.push(transcript)
      }
      const text = fragments.join(' ').trim()
      if (text) onResultRef.current(text)
    }
    recognition.onerror = (event) => {
      if (recognitionRef.current !== recognition) return
      console.warn('语音识别错误:', event.error)
      finish(recognition, true)
    }
    recognition.onend = () => {
      finish(recognition)
    }

    recognitionRef.current = recognition
    try {
      recognition.start()
      if (recognitionRef.current === recognition) setListening(true)
    } catch (error) {
      finish(recognition, true)
      console.warn('无法启动语音识别:', error)
    }
  }, [finish])

  useEffect(() => () => {
    const recognition = recognitionRef.current
    if (!recognition) return
    recognitionRef.current = null
    detachRecognition(recognition, true)
  }, [])

  return { listening, supported, start, stop }
}
