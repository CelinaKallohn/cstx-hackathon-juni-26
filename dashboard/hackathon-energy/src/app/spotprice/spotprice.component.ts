import { Component, ElementRef, ViewChild, AfterViewInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import * as echarts from 'echarts/core';
import { LineChart } from 'echarts/charts';
import { TitleComponent, TooltipComponent, GridComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import { Subscription } from 'rxjs';
import { DateSelectionService } from '../services/date-selection.service';
import { CsvDataService } from '../services/csv-data.service';

// Register the required ECharts components
echarts.use([LineChart, TitleComponent, TooltipComponent, GridComponent, LegendComponent, CanvasRenderer]);

@Component({
  selector: 'app-spotprice',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './spotprice.component.html',
  styleUrls: ['./spotprice.component.css'],
})
export class Spotprice implements AfterViewInit, OnDestroy {
  @ViewChild('chart', { static: true }) chartEl!: ElementRef<HTMLDivElement>;
  private chartInstance: echarts.ECharts | null = null;
  // parsed data: map date (DD.MM.YYYY) -> { times: string[], prices: number[] }
  private dataByDate: Record<string, { times: string[]; prices: number[] }> = {};
  dates: string[] = [];
  selectedDateIndex = 0;
  // global min/max across the whole CSV (used for fixed yAxis)
  private globalMin = Number.POSITIVE_INFINITY;
  private globalMax = Number.NEGATIVE_INFINITY;
  // no padding: axis will be fixed to default range unless day's values exceed it
  // default fixed axis range (ct/kWh)
  private defaultYMin = -5;
  private defaultYMax = 50;

  get selectedDate(): string | null {
    return this.dates.length > 0 ? this.dates[this.selectedDateIndex] : null;
  }

  get selectedDateAsIso(): string {
    const sd = this.selectedDate;
    if (!sd) return '';
    const parts = sd.split('.');
    if (parts.length !== 3) return '';
    return `${parts[2]}-${parts[1].padStart(2, '0')}-${parts[0].padStart(2, '0')}`;
  }

  private dateSub?: Subscription;

  constructor(private dateService: DateSelectionService, private csvService: CsvDataService) {}

  ngAfterViewInit(): void {
    // keep lifecycle hook synchronous and delegate async work
    this.chartInstance = echarts.init(this.chartEl.nativeElement);
    this.initChart().catch(err => console.error('Fehler beim Initialisieren des Charts:', err));
  }

  private async initChart(): Promise<void> {
    try {
      await this.csvService.ensureLoaded();
      // get available dates from service
      this.dates = this.csvService.getAvailableDates();
      this.selectedDateIndex = Math.max(0, this.dates.length - 1);

      // compute global bounds by scanning all dates (fallback behaviour preserved)
      for (const d of this.dates) {
        const data = this.csvService.getSpotDataByDate(d);
        if (!data || data.prices.length === 0) continue;
        const dayMin = Math.min(...data.prices);
        const dayMax = Math.max(...data.prices);
        if (dayMin < this.globalMin) this.globalMin = dayMin;
        if (dayMax > this.globalMax) this.globalMax = dayMax;
      }

      // build initial chart with selected date (default latest)
      if (this.selectedDate) {
        this.updateChartForDate(this.selectedDate);
      }

      // publish available dates to the global service and subscribe to changes
      this.dateService.setAvailableDates(this.dates);
      this.dateService.setSelectedIndex(this.selectedDateIndex);
       this.dateSub = this.dateService.date$.subscribe(iso => {
         if (!iso) return;
         const parts = iso.split('-');
         if (parts.length !== 3) return;
         const dateStr = `${parts[2].padStart(2, '0')}.${parts[1].padStart(2, '0')}.${parts[0]}`;
         const idx = this.dates.indexOf(dateStr);
         if (idx !== -1 && idx !== this.selectedDateIndex) {
           this.selectedDateIndex = idx;
           this.updateChartForDate(dateStr);
         } else if (idx === -1 && !this.dates.includes(dateStr)) {
           // Date not available, show empty chart
           this.updateChartForDate(dateStr);
         }
       });

      window.addEventListener('resize', this.onResize);
    } catch (err) {
      console.error('Fehler beim Laden/Parsen der CSV-Datei:', err);
    }
  }

    private updateChartForDate(date: string) {
      const data = this.csvService.getSpotDataByDate(date);
      const simulationData = this.csvService.getSpotSimulationDataByDate(date);
      const predictionData = this.csvService.getSpotPredictionDataByDate(date);

      // Use real data if available, otherwise simulation data
      const hasRealData = data && data.prices.length > 0 && data.times.length > 0;
      const hasSimulationData = !hasRealData && simulationData && simulationData.prices.length > 0;
      const hasPredictionData = predictionData && predictionData.prices.length > 0;

       if (!hasRealData && !hasSimulationData && !hasPredictionData) {
         // Show empty chart if no data available
         const emptyOption: any = {
           title: { text: `Spotmarktpreis`, textStyle: { color: '#000' } },
           legend: {
             data: ['Spotpreis', 'Vorhersage-Spotpreis'],
             top: '4%',
             left: 'center',
             textStyle: { color: '#000' },
           },
           tooltip: { trigger: 'axis' },
           xAxis: { type: 'category', data: [], axisLabel: { color: '#000' } },
           yAxis: { type: 'value', name: 'Preis (ct/kWh)', axisLabel: { color: '#000' } },
           series: [],
           grid: { left: '10%', right: '10%', bottom: '15%' },
         };
         this.chartInstance?.setOption(emptyOption, true);
         return;
       }

      // Use real or simulation data
      const dataSource = hasRealData ? data : (hasSimulationData ? simulationData : null);

      // Create full 24-hour time axis
      const fullTimes = [];
      for (let h = 0; h < 24; h++) {
        fullTimes.push(`${String(h).padStart(2, '0')}:00:00`);
      }

      // Map data to the full 24-hour axis
      const mapDataToFullAxis = (sourceTimes: string[], sourceData: any[]): any[] => {
        const fullData = new Array(24).fill(null);

        for (let i = 0; i < sourceTimes.length; i++) {
          const time = sourceTimes[i];
          const hour = parseInt(time.split(':')[0]);
          if (hour >= 0 && hour < 24) {
            fullData[hour] = sourceData[i];
          }
        }

        return fullData;
      };

      // Map prices to the full axis
      const fullPrices = (hasRealData || hasSimulationData) && dataSource ? mapDataToFullAxis(dataSource.times, dataSource.prices) : new Array(24).fill(null);
      const fullPredictionPrices = hasPredictionData ? mapDataToFullAxis(predictionData.times, predictionData.prices) : new Array(24).fill(null);

      // Calculate bounds including both real/simulation and prediction data
      let dayMin = Number.POSITIVE_INFINITY;
      let dayMax = Number.NEGATIVE_INFINITY;

      const numericRealPrices = fullPrices.filter((p): p is number => p !== null && p !== undefined);
      if (numericRealPrices.length > 0) {
        dayMin = Math.min(dayMin, Math.min(...numericRealPrices));
        dayMax = Math.max(dayMax, Math.max(...numericRealPrices));
      }

      const numericPredictionPrices = fullPredictionPrices.filter((p): p is number => p !== null && p !== undefined);
      if (numericPredictionPrices.length > 0) {
        dayMin = Math.min(dayMin, Math.min(...numericPredictionPrices));
        dayMax = Math.max(dayMax, Math.max(...numericPredictionPrices));
      }

      let bounds: { min: number; max: number };
      if (dayMin >= this.defaultYMin && dayMax <= this.defaultYMax) {
        bounds = { min: this.defaultYMin, max: this.defaultYMax };
      } else {
        bounds = this.getBoundsWithDefaults(dayMin, dayMax);
      }

      const option: any = {
         title: { text: `Spotmarktpreis`, textStyle: { color: '#000' } },
         legend: {
           data: ['Spotpreis', 'Vorhersage-Spotpreis'],
           top: '4%',
           left: 'center',
           textStyle: { color: '#000' },
         },
         tooltip: { trigger: 'axis' },
         xAxis: {
           type: 'category',
           data: fullTimes,
           boundaryGap: false,
           axisLabel: {
             interval: 2,
             color: '#000',
             formatter: (value: string) => {
               const parts = value.split(':');
               return parts.length >= 2 ? `${parts[0]}:${parts[1]}` : value;
             },
           },
           axisName: { textStyle: { color: '#000' } },
         },
         yAxis: {
           type: 'value',
           name: 'Preis (ct/kWh)',
           min: bounds.min,
           max: bounds.max,
           axisLabel: { color: '#000' },
           axisName: { textStyle: { color: '#000' } },
         },
         series: [],
         grid: { left: '10%', right: '10%', bottom: '15%' },
       };

      // Add real or simulation data if available
      if (hasRealData || hasSimulationData) {
        option.series.push({
          name: 'Spotpreis',
          type: 'line',
          data: fullPrices,
          smooth: true,
          showSymbol: false,
          itemStyle: { color: '#ff9100' },
          areaStyle: { opacity: 0.12 },
        });
      }

      // Add prediction data if available (light blue)
      if (hasPredictionData && fullPredictionPrices.some(p => p !== null && p !== undefined)) {
        option.series.push({
          name: 'Vorhersage-Spotpreis',
          type: 'line',
          data: fullPredictionPrices,
          smooth: true,
          showSymbol: false,
          lineStyle: { width: 2, color: '#87CEEB' },
          itemStyle: { color: '#87CEEB' },
          areaStyle: { opacity: 0.12 },
        });
      }

      this.chartInstance?.setOption(option, true);
    }

  private getBoundsWithDefaults(dayMin: number, dayMax: number): { min: number; max: number } {
    if (dayMin === Number.POSITIVE_INFINITY || dayMax === Number.NEGATIVE_INFINITY) {
      return { min: this.defaultYMin, max: this.defaultYMax };
    }
    let minBound = dayMin < this.defaultYMin ? dayMin : this.defaultYMin;
    let maxBound = dayMax > this.defaultYMax ? dayMax : this.defaultYMax;
    // ensure non-zero range
    if (minBound === maxBound) {
      const eps = Math.abs(minBound) * 1e-6 || 1e-3;
      minBound -= eps;
      maxBound += eps;
    }
    return { min: minBound, max: maxBound };
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
