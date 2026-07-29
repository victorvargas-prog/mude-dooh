export default async function handler(req, res) {
  const { action = 'data', city, key } = req.query;
  const APPS_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbyd5TUjYxF1Mu9u1F1q3abuMs09z0oAObw3xEcKTSDr0st6AOh6fe-yz1NhdVe_JObE1A/exec';

  const url = new URL(APPS_SCRIPT_URL);
  url.searchParams.set('key', key || '');
  url.searchParams.set('action', action);
  if (city) url.searchParams.set('city', city);

  try {
    const r = await fetch(url.toString());
    const data = await r.json();
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.status(200).json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
}
