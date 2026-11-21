export default function ErrorBox({ message }: { message: string }) {
  return (
    <div className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      <p className="font-medium">Something went wrong</p>
      <p className="mt-1 break-words">{message}</p>
      <p className="mt-1 text-red-700">Is the Django server running (python manage.py runserver)?</p>
    </div>
  );
}
