/** Shows `text` with the first case-insensitive match of `quote` highlighted. */
export default function Highlight({ text, quote }: { text: string; quote: string }) {
  const index = quote ? text.toLowerCase().indexOf(quote.toLowerCase()) : -1;
  if (index < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, index)}
      <mark className="rounded bg-yellow-200 px-0.5">{text.slice(index, index + quote.length)}</mark>
      {text.slice(index + quote.length)}
    </>
  );
}
