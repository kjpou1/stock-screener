export const MARKET_FLAGS = {
  US: '🇺🇸',
  HK: '🇭🇰',
  IN: '🇮🇳',
  JP: '🇯🇵',
  KR: '🇰🇷',
  TW: '🇹🇼',
  CN: '🇨🇳',
  DE: '🇩🇪',
  CA: '🇨🇦',
  SG: '🇸🇬',
  MY: '🇲🇾',
  AU: '🇦🇺',
};

export function marketFlag(code) {
  if (!code) return '';
  return MARKET_FLAGS[code.toUpperCase()] || '';
}
