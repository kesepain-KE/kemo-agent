import styles from './PlainTextMessage.module.css'

export interface PlainTextMessageProps {
  content: string
  className?: string
}

export function PlainTextMessage({ content, className = '' }: PlainTextMessageProps) {
  return <div className={`${styles.root} ${className}`.trim()}>{content}</div>
}
