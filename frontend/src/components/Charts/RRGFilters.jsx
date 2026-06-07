/**
 * RRG filter controls: a current-rank range slider + a name multi-select.
 * Presentational only — state lives in useRRGFilters. Shared by the live and
 * static Group Rankings pages via RRGChart.
 */
import { Autocomplete, TextField, Slider, Box, Typography } from '@mui/material';

export default function RRGFilters({
  scopeLabel,
  names,
  selected,
  onSelected,
  maxRank,
  rankValue,
  onRankChange,
}) {
  return (
    <>
      {maxRank > 1 && (
        <Box sx={{ width: 200 }}>
          <Typography variant="caption" color="text.secondary">
            Rank {rankValue[0]}–{rankValue[1]}
          </Typography>
          <Slider
            size="small"
            min={1}
            max={maxRank}
            value={rankValue}
            onChange={(_e, v) => onRankChange(v)}
            valueLabelDisplay="auto"
            disableSwap
            sx={{ mt: -0.5 }}
          />
        </Box>
      )}
      <Autocomplete
        multiple
        size="small"
        options={names}
        value={selected}
        onChange={(_e, v) => onSelected(v)}
        limitTags={3}
        disableCloseOnSelect
        renderInput={(params) => (
          <TextField
            {...params}
            label={`Filter ${scopeLabel.toLowerCase()}`}
            placeholder={selected.length ? '' : 'All shown'}
          />
        )}
        sx={{ width: 360, maxWidth: '100%' }}
      />
    </>
  );
}
