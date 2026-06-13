import { MenuItem, Select } from '@mui/material';

import { useMarket } from '../../contexts/MarketContext';
import { marketFlag } from '../../utils/marketFlags';

function MarketSelector() {
  const { selectedMarket, setSelectedMarket, selectableMarkets, marketLabel } = useMarket();

  if (selectableMarkets.length < 2) {
    return null;
  }

  return (
    <Select
      value={selectedMarket}
      onChange={(event) => setSelectedMarket(event.target.value)}
      size="small"
      variant="standard"
      disableUnderline
      inputProps={{ 'aria-label': 'Market selector' }}
      renderValue={(code) => `${marketFlag(code)} ${code}`}
      sx={{
        ml: 1.5,
        px: 1,
        py: 0.25,
        fontSize: '13px',
        fontWeight: 600,
        color: 'inherit',
        backgroundColor: 'rgba(255,255,255,0.15)',
        borderRadius: 1,
        '& .MuiSelect-icon': { color: 'rgba(255,255,255,0.7)' },
        '&:hover': { backgroundColor: 'rgba(255,255,255,0.25)' },
      }}
    >
      {selectableMarkets.map((code) => (
        <MenuItem key={code} value={code}>
          {`${marketFlag(code)}  ${marketLabel(code)}`}
        </MenuItem>
      ))}
    </Select>
  );
}

export default MarketSelector;
