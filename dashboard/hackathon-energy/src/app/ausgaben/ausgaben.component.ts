import { Component, ElementRef, ViewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import * as echarts from 'echarts/core';
import { BarChart } from 'echarts/charts';
import { TitleComponent, TooltipComponent, GridComponent, LegendComponent, MarkLineComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Subscription } from 'rxjs';
import { DateSelectionService } from '../services/date-selection.service';

// Register required ECharts components for stacked bar chart with markline
echarts.use([BarChart, TitleComponent, TooltipComponent, GridComponent, LegendComponent, MarkLineComponent, CanvasRenderer]);

interface DayData {
  times: string[];
  taxesAndCharges: number[]; // ct/kWh -> € conversion
  workPrice: number[]; // ct/kWh -> € conversion
  spotPrice: number[]; // ct/kWh -> € conversion
}

@Component({
  selector: 'app-ausgaben',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './ausgaben.component.html',
  styleUrls: ['./ausgaben.component.css'],
})
export class Ausgaben implements AfterViewInit, OnDestroy {
  @ViewChild('chart', { static: true }) chartEl!: ElementRef<HTMLDivElement>;
  private chartInstance: echarts.ECharts | null = null;

  // parsed data: date (DD.MM.YYYY) -> { times, taxesAndCharges, workPrice, spotPrice }
  private dataByDate: Record<string, DayData> = {};
  dates: string[] = [];
  selectedDateIndex = 0;

  private dateSub?: Subscription;
  private readonly referencePrice = 59; // ct (customer price)

  constructor(private readonly dateService: DateSelectionService) {}

  ngAfterViewInit(): void {
    this.chartInstance = echarts.init(this.chartEl.nativeElement);
    this.init().catch(err => console.error('Fehler beim Initialisieren Ausgaben:', err));
  }

  private async init(): Promise<void> {
    try {
      const resp = await fetch('/collected_cleaned_data.csv');
      if (!resp.ok) throw new Error('Konnte CSV nicht laden');
      const txt = await resp.text();
      this.parseCsv(txt);

      const initDate = this.selectedDate();
      if (initDate) {
        this.updateChartForDate(initDate);
      }

      // Merge available dates from other components
      const currentDates = (this.dateService as any)['datesSubject'].getValue() || [];
      const mergedDates = Array.from(new Set([...currentDates, ...this.dates])).sort((a, b) => {
        const pa = a.split('.').map(Number);
        const pb = b.split('.').map(Number);
        const da = new Date(pa[2], pa[1] - 1, pa[0]);
        const db = new Date(pb[2], pb[1] - 1, pb[0]);
        return da.getTime() - db.getTime();
      });
      if (mergedDates.length > 0) {
        this.dateService.setAvailableDates(mergedDates);
      }

      // Subscribe for date changes
      this.dateSub = this.dateService.date$.subscribe(iso => {
        if (!iso) return;
        const parts = iso.split('-');
        if (parts.length !== 3) return;
        const dateStr = `${parts[2].padStart(2, '0')}.${parts[1].padStart(2, '0')}.${parts[0]}`;
        const idx = this.dates.indexOf(dateStr);
        if (idx !== -1 && idx !== this.selectedDateIndex) {
          this.selectedDateIndex = idx;
          if (dateStr) {
            this.updateChartForDate(dateStr);
          }
        }
      });

      window.addEventListener('resize', this.onResize);
    } catch (err) {
      console.error(err);
    }
  }

  public selectedDate(): string | null {
    return this.dates.length > 0 ? this.dates[this.selectedDateIndex] : null;
  }

  private parseCsv(content: string) {
    const lines = content.split(/\r?\n/).map(l => l.trim()).filter(l => l.length > 0);
    if (lines.length === 0) return;

    // header: Datum;Uhrzeit;"Profilwert kWh";"Profilwert kW";Spotmarktpreis in ct/kWh;Endkundenpreis in ct/kwh;Arbeitspreis Umspannung ct/kwh;Steuern&Abgaben in ct/kwh;...
    const header = lines[0].split(';').map(h => h.toLowerCase().replaceAll('"', ''));
    const dateIdx = Math.max(0, header.findIndex(h => h.includes('datum')));
    const timeIdx = Math.max(1, header.findIndex(h => h.includes('uhrzeit')));
    const spotIdx = Math.max(4, header.findIndex(h => h.includes('spotmarktpreis')));
    const workIdx = Math.max(6, header.findIndex(h => h.includes('arbeitspreis')));
    const taxIdx = Math.max(7, header.findIndex(h => h.includes('steuern') || h.includes('abgaben')));

    for (let i = 1; i < lines.length; i++) {
      const cols = lines[i].split(';').map(c => c.replaceAll('"', ''));
      if (cols.length <= Math.max(dateIdx, timeIdx, spotIdx, workIdx, taxIdx)) continue;

      // Normalize date: "1.1.2025" -> "01.01.2025"
      let dateStr = cols[dateIdx];
      const dateParts = dateStr.split('.');
      if (dateParts.length !== 3) continue;
      dateStr = `${dateParts[0].padStart(2, '0')}.${dateParts[1].padStart(2, '0')}.${dateParts[2]}`;

      const time = cols[timeIdx]; // HH:MM:SS

      // Parse values (ct/kWh) - keep in ct
      let spotStr = cols[spotIdx] || '';
      spotStr = spotStr.replaceAll('.', '').replace(',', '.');
      const spotPrice = Number.parseFloat(spotStr); // in ct

      let workStr = cols[workIdx] || '';
      workStr = workStr.replaceAll('.', '').replace(',', '.');
      const workPrice = Number.parseFloat(workStr); // in ct

      let taxStr = cols[taxIdx] || '';
      taxStr = taxStr.replaceAll('.', '').replace(',', '.');
      const taxPrice = Number.parseFloat(taxStr); // in ct

      if (Number.isNaN(spotPrice) || Number.isNaN(workPrice) || Number.isNaN(taxPrice)) continue;

      if (!this.dataByDate[dateStr]) {
        this.dataByDate[dateStr] = { times: [], taxesAndCharges: [], workPrice: [], spotPrice: [] };
      }
      this.dataByDate[dateStr].times.push(time);
      this.dataByDate[dateStr].taxesAndCharges.push(taxPrice + workPrice); // Combined segment in ct
      this.dataByDate[dateStr].workPrice.push(0); // Not used separately
      this.dataByDate[dateStr].spotPrice.push(spotPrice); // in ct
    }

    this.dates = Object.keys(this.dataByDate).sort((a, b) => {
      const pa = a.split('.').map(Number);
      const pb = b.split('.').map(Number);
      const da = new Date(pa[2], pa[1] - 1, pa[0]);
      const db = new Date(pb[2], pb[1] - 1, pb[0]);
      return da.getTime() - db.getTime();
    });
    this.selectedDateIndex = Math.max(0, this.dates.length - 1);
  }

  private updateChartForDate(date: string) {
    const d = this.dataByDate[date];
    if (!d) return;

    // Calculate max value: sum of both segments for each time point
    const maxValues = d.taxesAndCharges.map((tax, i) => tax + d.spotPrice[i]);
    const dayMax = maxValues.length ? Math.max(...maxValues) : 0;

    // Ensure at least 70ct is shown (so reference line at 59ct is visible), but expand if needed
    const yAxisMax = Math.max(70, dayMax * 1.1); // Add 10% margin if exceeds 70

    const option: any = {
      title: { text: `Ausgaben` },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: any) => {
          if (!Array.isArray(params)) params = [params];
          let result = params[0].axisValue + '<br/>';
          for (const p of params) {
            if (p.seriesName === 'Steuern & Arbeitspreis') {
              result += `${p.seriesName}: ${p.value.toFixed(2)}ct<br/>`;
            } else if (p.seriesName === 'Spotpreis') {
              result += `${p.seriesName}: ${p.value.toFixed(2)}ct<br/>`;
            }
          }
          return result;
        },
      },
      xAxis: {
        type: 'category',
        data: d.times,
        boundaryGap: true,
        name: 'Uhrzeit',
        axisLabel: {
          interval: 7,
          formatter: (value: string) => {
            const parts = value.split(':');
            return parts.length >= 2 ? `${parts[0]}:${parts[1]}` : value;
          },
        },
      },
      yAxis: { type: 'value', name: 'Ausgaben (ct)', min: 0, max: yAxisMax },
      series: [
        {
          name: 'Steuern & Arbeitspreis',
          type: 'bar',
          data: d.taxesAndCharges,
          stack: 'total',
          itemStyle: { color: '#5470C6', opacity: 0.5 },
        },
        {
          name: 'Spotpreis',
          type: 'bar',
          data: d.spotPrice,
          stack: 'total',
          itemStyle: { color: '#EE6666' },
          markLine: {
            data: [
              {
                name: 'Kundenpreis (59ct)',
                yAxis: this.referencePrice,
                lineStyle: { color: '#333', type: 'solid', width: 2 },
                label: { position: 'insideEndTop', offset: [-10, -5] },
              },
            ],
          },
        },
      ],
      grid: { left: '10%', right: '10%', bottom: '15%' },
    };
    this.chartInstance?.setOption(option);
  }

  private readonly onResize = () => {
    if (this.chartInstance) this.chartInstance.resize();
  };

  ngOnDestroy(): void {
    window.removeEventListener('resize', this.onResize);
    if (this.chartInstance) {
      this.chartInstance.dispose();
      this.chartInstance = null;
    }
    if (this.dateSub) this.dateSub.unsubscribe();
  }
}

